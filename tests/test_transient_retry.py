from types import SimpleNamespace
from unittest.mock import patch

import requests

from request_pipeline import db, errors, run


class _FakeCursor:
    def __init__(self, recorder, rowcount=1):
        self._recorder = recorder
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self._recorder.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, recorder, rowcount=1):
        self._recorder = recorder
        self._rowcount = rowcount

    def cursor(self, dictionary=False):
        return _FakeCursor(self._recorder, self._rowcount)

    def commit(self):
        pass

    def close(self):
        pass


def _settings(**overrides):
    base = dict(
        max_retry_count=3,
        transient_retry_delay_seconds=300,
        failed_retry_cooldown_minutes=10,
        max_failed_recovery_rounds=5,
        max_analysis_per_run=3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(
        f"{status_code} Server Error for url: https://example.test/internal/email-analysis",
        response=response,
    )


def test_bad_gateway_is_transient():
    assert errors.classify_error(_http_error(502)) == errors.TRANSIENT


def test_retryable_status_codes_are_transient():
    for status_code in (408, 425, 429, 500, 502, 503, 504):
        assert errors.classify_error(_http_error(status_code)) == errors.TRANSIENT


def test_client_errors_are_permanent():
    for status_code in (400, 401, 403, 404, 422):
        assert errors.classify_error(_http_error(status_code)) == errors.PERMANENT


def test_connection_and_timeout_failures_are_transient():
    transient_exceptions = (
        requests.exceptions.ConnectionError("Connection refused"),
        requests.exceptions.ConnectTimeout("connect timeout"),
        requests.exceptions.ReadTimeout("read timeout"),
        requests.exceptions.ChunkedEncodingError("truncated response"),
        errors.GatewayBlockedError("gateway blocked"),
    )
    for exc in transient_exceptions:
        assert errors.classify_error(exc) == errors.TRANSIENT


def test_ssl_and_application_errors_are_permanent():
    # SSLError는 ConnectionError의 하위 클래스지만 설정 오류이므로 영구 오류입니다.
    assert errors.classify_error(requests.exceptions.SSLError("bad ca")) == errors.PERMANENT
    assert errors.classify_error(RuntimeError("Unexpected API status: ERROR")) == errors.PERMANENT
    assert errors.classify_error(ValueError("not json")) == errors.PERMANENT


def test_analyze_row_reports_the_error_kind():
    row = {"id": 7, "route_action_json": {"api_profile": "defect-analysis"}}

    with (
        patch("request_pipeline.run.settings", _settings()),
        patch("request_pipeline.run.db.claim_api_request", return_value=True),
        patch("request_pipeline.run.db.get_api_profile", return_value={}),
        patch(
            "request_pipeline.run.analyze_request",
            side_effect=_http_error(502),
        ),
        patch("request_pipeline.run.db.mark_retry") as mark_retry,
    ):
        assert run._analyze_row(row) is False

    assert mark_retry.call_args.args[1] == 7
    assert mark_retry.call_args.args[3] == errors.TRANSIENT


def test_mark_retry_delays_transient_failures():
    recorder = []
    settings = _settings()
    with patch.object(db, "connect", return_value=_FakeConnection(recorder)):
        db.mark_retry(settings, 7, "502 Server Error", errors.TRANSIENT)

    sql, params = recorder[0]
    assert "next_attempt_at=IF( %s > 0, DATE_ADD(NOW(), INTERVAL %s SECOND), NULL )" in sql
    assert params == (3, "502 Server Error", errors.TRANSIENT, 300, 300, 7)


def test_mark_retry_does_not_delay_permanent_failures():
    recorder = []
    settings = _settings()
    with patch.object(db, "connect", return_value=_FakeConnection(recorder)):
        db.mark_retry(settings, 7, "401 Client Error", errors.PERMANENT)

    _, params = recorder[0]
    assert params == (3, "401 Client Error", errors.PERMANENT, 0, 0, 7)


def test_mark_retry_defaults_to_permanent():
    recorder = []
    with patch.object(db, "connect", return_value=_FakeConnection(recorder)):
        db.mark_retry(_settings(), 7, "boom")

    _, params = recorder[0]
    assert params[2] == errors.PERMANENT


def test_recover_failed_transient_requests_only_targets_classified_rows():
    recorder = []
    settings = _settings()
    with patch.object(db, "connect", return_value=_FakeConnection(recorder, rowcount=2)):
        assert db.recover_failed_transient_requests(settings) == 2

    sql, params = recorder[0]
    assert "SET status='RETRY', retry_count=0, recovery_round=recovery_round+1" in sql
    assert "status='FAILED'" in sql
    assert "last_error_kind=%s" in sql
    assert "updated_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)" in sql
    assert "(%s = 0 OR recovery_round < %s)" in sql
    assert params == (errors.TRANSIENT, 10, 5, 5)


def test_list_api_ready_skips_rows_inside_the_retry_delay():
    recorder = []
    with patch.object(db, "connect", return_value=_FakeConnection(recorder)):
        db.list_api_ready(_settings(), 3)

    sql, _ = recorder[0]
    assert "(next_attempt_at IS NULL OR next_attempt_at <= NOW())" in sql


def test_claim_clears_the_previous_error_kind():
    recorder = []
    with patch.object(db, "connect", return_value=_FakeConnection(recorder)):
        db.claim_api_request(_settings(), 7)

    sql, _ = recorder[0]
    assert "last_error_kind=NULL" in sql
    assert "(next_attempt_at IS NULL OR next_attempt_at <= NOW())" in sql


def test_run_once_requeues_transient_failures_before_processing():
    call_order = []

    with (
        patch("request_pipeline.run.settings", _settings()),
        patch.object(
            run.db,
            "recover_incomplete_requests",
            side_effect=lambda settings: call_order.append("recover_processing")
            or {"processing": 0},
        ),
        patch.object(
            run.db,
            "recover_failed_transient_requests",
            side_effect=lambda settings: call_order.append("recover_failed") or 1,
        ) as recover_failed,
        patch.object(run, "collect_legacy_pop3_mail", return_value=0),
        patch.object(
            run,
            "process_pending_send",
            side_effect=lambda: call_order.append("send")
            or run._empty_mail_counts(),
        ),
        patch.object(
            run,
            "process_api_queue",
            side_effect=lambda batch_size: call_order.append("api")
            or (0, True, run._empty_mail_counts()),
        ),
    ):
        run._run_once()

    recover_failed.assert_called_once()
    assert call_order == ["recover_processing", "recover_failed", "send", "api"]
