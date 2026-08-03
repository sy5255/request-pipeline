import json
import logging
import time

from request_pipeline import db, mail_queue
from request_pipeline.config import settings
from request_pipeline.mail_client import iter_messages
from request_pipeline.mail_parser import parse_mail
from request_pipeline.mail_sender import (
    MailSendError,
    MailSendUnknownError,
    send_analysis_mail,
)
from request_pipeline.web_client import analyze_request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("request_pipeline")


def _profile_key_from_row(row: dict) -> str:
    action = row.get("route_action_json") or {}
    if isinstance(action, str):
        action = json.loads(action)
    profile_key = str(action.get("api_profile") or "").strip()
    if not profile_key:
        raise RuntimeError(
            f"API profile is missing for request_id={row.get('id')} "
            f"rule={row.get('route_rule_key')}"
        )
    return profile_key


def _legacy_pop3_collection_enabled() -> bool:
    if not hasattr(settings, "pop3_collection_enabled"):
        logger.warning(
            "settings.pop3_collection_enabled is missing; "
            "legacy POP3 collection defaults to disabled."
        )
        return False
    return bool(getattr(settings, "pop3_collection_enabled", False))


def _analyze_row(row: dict) -> bool:
    request_id = int(row["id"])
    if not db.claim_api_request(settings, request_id):
        logger.info(
            "analysis claim skipped request_id=%s route_type=%s status=%s",
            request_id,
            row.get("route_type"),
            row.get("status"),
        )
        return True

    try:
        profile_key = _profile_key_from_row(row)
        profile = db.get_api_profile(settings, profile_key)
        result = analyze_request(
            settings,
            profile=profile,
            request_id=request_id,
            requester_user_id=row.get("requester_user_id") or "",
            requester_email=row.get("sender_email") or "",
            request_title=row.get("request_title") or "",
            mail_body=row.get("mail_body") or "",
            route_case=row.get("route_case"),
            route_rule_key=row.get("route_rule_key"),
        )
        db.mark_completed(settings, request_id, result)
        logger.info(
            "analysis completed request_id=%s route_case=%s rule=%s profile=%s",
            request_id,
            row.get("route_case"),
            row.get("route_rule_key"),
            profile_key,
        )
        return True
    except Exception as exc:
        db.mark_retry(settings, request_id, str(exc))
        logger.exception("analysis failed request_id=%s", request_id)
        return False


def _sleep_between_requests() -> None:
    if settings.analysis_interval_seconds > 0:
        time.sleep(settings.analysis_interval_seconds)


def process_api_queue(batch_size: int) -> tuple[int, bool]:
    if batch_size < 1:
        raise RuntimeError("API queue batch size must be at least 1")

    processed_count = 0
    batch_number = 0
    while True:
        rows = db.list_api_ready(settings, batch_size)
        if not rows:
            if batch_number == 0:
                logger.info(
                    "api queue ready=0 batch_size=%s processed_total=0",
                    batch_size,
                )
            break

        batch_number += 1
        logger.info(
            "api queue batch=%s ready=%s batch_size=%s processed_total=%s",
            batch_number,
            len(rows),
            batch_size,
            processed_count,
        )

        for row in rows:
            if processed_count > 0:
                _sleep_between_requests()
            if not _analyze_row(row):
                logger.warning(
                    "api queue processing stopped after failure request_id=%s "
                    "processed_total=%s",
                    row["id"],
                    processed_count,
                )
                return processed_count, False
            processed_count += 1

        if len(rows) < batch_size:
            break

    return processed_count, True


def collect_legacy_pop3_mail() -> int:
    if not _legacy_pop3_collection_enabled():
        return 0

    collected = 0
    for raw_mail in iter_messages(settings):
        if db.uidl_exists(settings, raw_mail.uidl):
            continue

        parsed = parse_mail(raw_mail.raw_bytes, settings.target_subject_prefix)
        if parsed.request_title is None:
            logger.info("legacy collector skipped non-api mail uidl=%s", raw_mail.uidl)
            continue

        request_id = db.insert_received(settings, raw_mail.uidl, parsed)
        collected += 1
        logger.info("legacy API mail routed request_id=%s", request_id)

    return collected


def process_pending_send() -> dict[str, int]:
    counts = {"ready": 0, "sent": 0, "failed": 0, "unknown": 0, "skipped": 0}

    # 이 플래그 하나로 분석 기능은 유지하면서 자동 메일링만 즉시 중단합니다.
    if not settings.mail_send_enabled:
        logger.info(
            "automatic mail delivery disabled MAIL_SEND_ENABLED=false; "
            "analysis results remain SEND_BLOCKED"
        )
        return counts

    recovered = mail_queue.recover_stale_sending(settings)
    if recovered:
        logger.warning("recovered stale mail sends as SEND_UNKNOWN count=%s", recovered)

    rows = mail_queue.list_send_ready(settings, settings.mail_send_batch_size)
    counts["ready"] = len(rows)
    logger.info(
        "mail queue ready=%s batch_size=%s recipient_mode=%s",
        len(rows),
        settings.mail_send_batch_size,
        settings.mail_recipient_mode,
    )

    for row in rows:
        request_id = int(row["id"])
        if not mail_queue.claim_send(settings, request_id):
            counts["skipped"] += 1
            logger.info("mail claim skipped request_id=%s", request_id)
            continue

        try:
            result = send_analysis_mail(settings, row)
            mail_queue.mark_sent(
                settings,
                request_id,
                sent_mail_id=result.mail_id,
                recipient=result.recipient,
                response_json=result.response_json,
            )
            counts["sent"] += 1
            logger.info(
                "mail sent request_id=%s recipient=%s mode=%s mail_id=%s",
                request_id,
                result.recipient,
                settings.mail_recipient_mode,
                result.mail_id or "<not-returned>",
            )
        except MailSendUnknownError as exc:
            mail_queue.mark_send_unknown(settings, request_id, str(exc))
            counts["unknown"] += 1
            logger.exception("mail send result unknown request_id=%s", request_id)
        except MailSendError as exc:
            mail_queue.mark_send_failed(settings, request_id, str(exc))
            counts["failed"] += 1
            logger.exception("mail send failed request_id=%s", request_id)
        except Exception as exc:
            mail_queue.mark_send_failed(settings, request_id, str(exc))
            counts["failed"] += 1
            logger.exception("mail preparation failed request_id=%s", request_id)

    return counts


def _run_once() -> None:
    recovery = db.recover_incomplete_requests(settings)
    if any(recovery.values()):
        logger.warning(
            "recovered incomplete API requests processing=%s",
            recovery["processing"],
        )

    legacy_collected = collect_legacy_pop3_mail()
    processed_count, succeeded = process_api_queue(settings.max_analysis_per_run)
    mail_counts = process_pending_send()

    if not succeeded:
        logger.warning("pipeline stopped because an API request failed")
        return

    logger.info(
        "pipeline finished legacy_collected=%s api_completed=%s "
        "queue_batch_size=%s mail_sent=%s mail_failed=%s mail_unknown=%s",
        legacy_collected,
        processed_count,
        settings.max_analysis_per_run,
        mail_counts["sent"],
        mail_counts["failed"],
        mail_counts["unknown"],
    )


def main() -> None:
    settings.validate()

    with db.pipeline_lock(settings) as acquired:
        if not acquired:
            logger.warning(
                "another pipeline run is active; current run skipped lock=%s",
                settings.pipeline_lock_name,
            )
            return

        logger.info("pipeline lock acquired lock=%s", settings.pipeline_lock_name)

        if db.migrate_legacy_table(settings):
            logger.warning(
                "legacy table migrated old=%s new=%s",
                db.LEGACY_MAIL_TABLE,
                db.MAIL_TABLE,
            )

        schema_result = db.ensure_schema(settings)
        if schema_result["backfilled_api_routes"]:
            logger.warning(
                "legacy API routes backfilled count=%s",
                schema_result["backfilled_api_routes"],
            )

        _run_once()


if __name__ == "__main__":
    main()
