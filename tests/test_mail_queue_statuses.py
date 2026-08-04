from request_pipeline.mail_queue import SEND_READY_STATUSES


def test_only_retryable_or_pending_states_are_auto_sent():
    assert SEND_READY_STATUSES == ("SEND_BLOCKED", "SEND_PENDING")


def test_manual_and_unknown_states_are_not_auto_sent():
    assert "SEND_DROPPED" not in SEND_READY_STATUSES
    assert "SEND_UNKNOWN" not in SEND_READY_STATUSES
