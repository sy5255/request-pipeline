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
    call_order = []

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
            side_effect=lambda settings: call_order.append("schema")
            or {"backfilled_api_routes": 0},
        ),
        patch.object(run, "_run_once", side_effect=lambda: call_order.append("run")),
    ):
        run.main()

    assert call_order == ["migrate", "schema", "run"]


def test_run_once_order():
    call_order = []

    with (
        patch.object(
            run.db,
            "recover_incomplete_requests",
            side_effect=lambda settings: call_order.append("recover")
            or {"processing": 1},
        ),
        patch.object(
            run,
            "collect_legacy_pop3_mail",
            side_effect=lambda: call_order.append("legacy_collect") or 0,
        ),
        patch.object(
            run,
            "process_api_queue",
            side_effect=lambda limit: call_order.append("api_queue") or (0, True),
        ),
        patch.object(
            run,
            "process_pending_send",
            side_effect=lambda: call_order.append("send"),
        ),
    ):
        run._run_once()

    assert call_order == ["recover", "legacy_collect", "api_queue", "send"]


def test_analyze_row_skips_non_claimable_row():
    row = {"id": 10, "route_type": "FILE_ARCHIVE", "status": "ROUTED"}

    with (
        patch.object(run.db, "claim_api_request", return_value=False),
        patch.object(run, "analyze_request") as analyze,
    ):
        result = run._analyze_row(row)

    assert result is True
    analyze.assert_not_called()
