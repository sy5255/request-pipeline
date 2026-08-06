#!/usr/bin/env python3
"""Run the request pipeline repeatedly within a scheduler time window."""

import logging
import os
import signal
import threading
import time

from request_pipeline.run import main as run_once


logger = logging.getLogger("request_pipeline.scheduler")
stop_event = threading.Event()

# Default scheduler cycle: process for 55 minutes, then remain idle until
# 58 minutes have elapsed. This leaves about one minute for the external
# scheduler to record successful completion before its 59-minute limit.
ACTIVE_WINDOW_SECONDS = int(
    os.getenv("PIPELINE_ACTIVE_WINDOW_SECONDS", str(55 * 60))
)
REST_WINDOW_SECONDS = int(
    os.getenv("PIPELINE_REST_WINDOW_SECONDS", str(3 * 60))
)
POLL_SECONDS = int(os.getenv("PIPELINE_POLL_SECONDS", "60"))


def _handle_stop_signal(signum, frame) -> None:
    logger.info("stop signal received signal=%s", signum)
    stop_event.set()


def _validate_timing() -> None:
    if ACTIVE_WINDOW_SECONDS < 1:
        raise RuntimeError("PIPELINE_ACTIVE_WINDOW_SECONDS must be at least 1")
    if REST_WINDOW_SECONDS < 0:
        raise RuntimeError("PIPELINE_REST_WINDOW_SECONDS must be at least 0")
    if POLL_SECONDS < 1:
        raise RuntimeError("PIPELINE_POLL_SECONDS must be at least 1")


def main() -> None:
    _validate_timing()

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    started_at = time.monotonic()
    active_deadline = started_at + ACTIVE_WINDOW_SECONDS
    shutdown_deadline = active_deadline + REST_WINDOW_SECONDS
    iteration = 0

    logger.info(
        "scheduled pipeline started active_window_seconds=%s "
        "rest_window_seconds=%s poll_seconds=%s",
        ACTIVE_WINDOW_SECONDS,
        REST_WINDOW_SECONDS,
        POLL_SECONDS,
    )

    while not stop_event.is_set():
        active_remaining = active_deadline - time.monotonic()
        if active_remaining <= 0:
            break

        iteration += 1
        logger.info(
            "pipeline iteration started iteration=%s active_remaining_seconds=%s",
            iteration,
            int(active_remaining),
        )

        try:
            run_once()
        except Exception:
            # Keep the scheduler process alive after a temporary DB/API failure.
            logger.exception("pipeline iteration failed iteration=%s", iteration)

        active_remaining = active_deadline - time.monotonic()
        if active_remaining <= 0 or stop_event.is_set():
            break

        sleep_seconds = min(POLL_SECONDS, active_remaining)
        logger.info(
            "pipeline iteration finished iteration=%s next_poll_seconds=%s",
            iteration,
            int(sleep_seconds),
        )
        stop_event.wait(timeout=sleep_seconds)

    if not stop_event.is_set():
        # Calculate the idle period from the original start time. If the last
        # pipeline iteration exceeded the 55-minute active window, shorten the
        # rest period so the whole process still finishes within 58 minutes.
        rest_seconds = max(0.0, shutdown_deadline - time.monotonic())
        logger.info(
            "pipeline active window finished iterations=%s rest_seconds=%s",
            iteration,
            int(rest_seconds),
        )
        stop_event.wait(timeout=rest_seconds)

    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "scheduled pipeline finished iterations=%s elapsed_seconds=%s",
        iteration,
        int(elapsed_seconds),
    )


if __name__ == "__main__":
    main()
