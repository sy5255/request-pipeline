from contextlib import contextmanager
from unittest.mock import patch

from request_pipeline import run


@contextmanager
def _lock_result(acquired: bool):
    yield acquired


def test_main_skips_when_pipeline_lock_is_not_acquired():
    with (
        patch.object(run.settings, "validate"),
        patch.object(run.db, "pipeline_lock", return_value=_lock_result(False)),
        patch.object(run.db, "migrate_legacy_table") as migrate,
        patch.object(run.db, "ensure_schema") as ensure_schema,
        patch.object(run, "_run_once") as run_once,
    ):
        run.main()

    migrate.assert_not_called()
    ensure_schema.assert_not_called()
    run_once.assert_not_called()


def test_main_migrates_and_ensures_schema_under_lock():
    call_order: list[str] = []

    with (
        patch.object(run.settings, "validate"),
        patch.object(run.db, "pipeline_lock", return_value=_lock_result(True)),
        patch.object(
            run.db,
            "migrate_legacy_table",
            side_effect=lambda settings: call_order.append("migrate") or True,
        ),
        patch.object(
            run.db,
            "ensure_schema",
            side_effect=lambda settings: call_order.append("schema"),
        ),
        patch.object(run, "_run_once", side_effect=lambda: call_order.append("run")),
    ):
        run.main()

    assert call_order == ["migrate", "schema", "run"]


def test_run_once_recovers_before_processing_retry_rows():
    call_order: list[str] = []

    with (
        patch.object(
            run.db,
            "recover_incomplete_requests",
            side_effect=lambda settings: call_order.append("recover")
            or {"received": 1, "processing": 1, "ignored": 0},
        ),
        patch.object(
            run,
            "retry_failed_analysis",
            side_effect=lambda limit: call_order.append("retry") or (0, True),
        ),
        patch.object(run, "process_pending_send"),
        patch.object(
            run,
            "collect_and_process_new_mail",
            side_effect=lambda limit: call_order.append("collect") or 0,
        ),
    ):
        run._run_once()

    assert call_order == ["recover", "retry", "collect"]


def test_failed_retry_stops_before_new_mail_collection():
    with (
        patch.object(
            run.db,
            "recover_incomplete_requests",
            return_value={"received": 0, "processing": 1, "ignored": 0},
        ),
        patch.object(run, "retry_failed_analysis", return_value=(0, False)),
        patch.object(run, "process_pending_send"),
        patch.object(run, "collect_and_process_new_mail") as collect,
    ):
        run._run_once()

    collect.assert_not_called()
