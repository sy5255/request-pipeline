from types import SimpleNamespace
from unittest.mock import call, patch

from request_pipeline.run import process_pending_send


def _settings():
    return SimpleNamespace(
        mail_send_enabled=True,
        mail_send_batch_size=3,
        mail_recipient_mode="TEST",
    )


def _send_result(request_id: int):
    return SimpleNamespace(
        mail_id=f"mail-{request_id}",
        recipient="tester@example.com",
        response_json={"mailId": f"mail-{request_id}"},
    )


def test_process_pending_send_drains_all_batches():
    batches = [
        [{"id": 1}, {"id": 2}, {"id": 3}],
        [{"id": 4}, {"id": 5}],
    ]

    with (
        patch("request_pipeline.run.settings", _settings()),
        patch("request_pipeline.run.mail_queue.recover_stale_sending", return_value=0),
        patch(
            "request_pipeline.run.mail_queue.list_send_ready",
            side_effect=batches,
        ) as list_ready,
        patch("request_pipeline.run.mail_queue.claim_send", return_value=True),
        patch(
            "request_pipeline.run.send_analysis_mail",
            side_effect=lambda settings, row: _send_result(int(row["id"])),
        ),
        patch("request_pipeline.run.mail_queue.mark_sent") as mark_sent,
    ):
        counts = process_pending_send()

    assert counts == {
        "ready": 5,
        "sent": 5,
        "failed": 0,
        "unknown": 0,
        "skipped": 0,
    }
    assert list_ready.call_args_list == [
        call(_settings(), 3, after_id=0),
        call(_settings(), 3, after_id=3),
    ]
    assert mark_sent.call_count == 5


def test_failed_row_is_not_retried_in_same_run():
    settings = _settings()
    batches = [
        [{"id": 10}, {"id": 11}, {"id": 12}],
        [{"id": 13}],
    ]

    def send(settings, row):
        if int(row["id"]) == 11:
            raise RuntimeError("payload error")
        return _send_result(int(row["id"]))

    with (
        patch("request_pipeline.run.settings", settings),
        patch("request_pipeline.run.mail_queue.recover_stale_sending", return_value=0),
        patch(
            "request_pipeline.run.mail_queue.list_send_ready",
            side_effect=batches,
        ) as list_ready,
        patch("request_pipeline.run.mail_queue.claim_send", return_value=True),
        patch("request_pipeline.run.send_analysis_mail", side_effect=send),
        patch("request_pipeline.run.mail_queue.mark_sent"),
        patch("request_pipeline.run.mail_queue.mark_send_failed") as mark_failed,
    ):
        counts = process_pending_send()

    assert counts["ready"] == 4
    assert counts["sent"] == 3
    assert counts["failed"] == 1
    assert list_ready.call_args_list == [
        call(settings, 3, after_id=0),
        call(settings, 3, after_id=12),
    ]
    mark_failed.assert_called_once()
