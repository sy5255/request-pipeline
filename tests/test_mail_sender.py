import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from request_pipeline.mail_sender import (
    MailSendUnknownError,
    build_contents,
    build_html_contents,
    build_payload,
    build_recipients,
    markdown_to_html,
    markdown_to_text,
    resolve_recipient,
    send_analysis_mail,
)


def _settings(**overrides):
    values = {
        "mail_recipient_mode": "TEST",
        "mail_test_recipient": "tester@example.com",
        "mail_allow_original_recipient": False,
        "mail_allowed_recipients": (
            "tester@example.com",
            "reply@example.com",
        ),
        "mail_subject_prefix": "[IFA Curator]",
        "knox_mail_doc_secu_type": "PERSONAL",
        "knox_mail_content_type": "TEXT",
        "knox_mail_sender_email": "agent@example.com",
        "knox_mail_api_url": "https://mail.example/api/v2.0/mails/send",
        "knox_mail_user_id": "agent",
        "knox_mail_auth_token": "token",
        "knox_mail_system_id": "KC123",
        "knox_mail_connect_timeout": 10,
        "knox_mail_read_timeout": 30,
        "knox_mail_verify": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _row():
    return {
        "id": 10,
        "request_title": "A.N3 CA Middle Void Reference TEM",
        "original_subject": "원본 제목",
        "sender_email": "requester@example.com",
        "reply_to_email": "reply@example.com",
        "answer_text": (
            "### 가장 가까운 이전 분석 레포트\n"
            "- **문서명:** 분석보고서 1\n"
            "- **연관 링크:** "
            "[https://edm.example/report/1](https://edm.example/report/1)"
        ),
    }


def _response(status_code=200, body='{"mailId":"mail-123"}'):
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_test_mode_forces_allowlisted_test_recipient():
    recipient = resolve_recipient(_settings(), _row())
    assert recipient == "tester@example.com"


def test_empty_allowlist_blocks_test_mode():
    settings = _settings(mail_allowed_recipients=())

    with pytest.raises(RuntimeError, match="allowlist is empty"):
        resolve_recipient(settings, _row())


def test_test_recipient_must_be_allowlisted():
    settings = _settings(mail_allowed_recipients=("other@example.com",))

    with pytest.raises(RuntimeError, match="not allowed"):
        resolve_recipient(settings, _row())


def test_allowlist_matching_is_case_insensitive():
    settings = _settings(
        mail_test_recipient="Tester@Example.com",
        mail_allowed_recipients=("tester@example.com",),
    )

    assert resolve_recipient(settings, _row()) == "Tester@Example.com"


def test_original_mode_requires_explicit_allow_flag():
    settings = _settings(
        mail_recipient_mode="ORIGINAL",
        mail_allow_original_recipient=False,
    )
    with pytest.raises(RuntimeError, match="blocked"):
        resolve_recipient(settings, _row())


def test_original_mode_prefers_allowlisted_reply_to_address():
    settings = _settings(
        mail_recipient_mode="ORIGINAL",
        mail_allow_original_recipient=True,
    )
    assert resolve_recipient(settings, _row()) == "reply@example.com"


def test_original_mode_rejects_non_allowlisted_reply_to_address():
    settings = _settings(
        mail_recipient_mode="ORIGINAL",
        mail_allow_original_recipient=True,
        mail_allowed_recipients=("tester@example.com",),
    )

    with pytest.raises(RuntimeError, match="not allowed"):
        resolve_recipient(settings, _row())


def test_markdown_links_are_converted_without_duplicate_label_for_text_mail():
    result = markdown_to_text(
        "### 보고서\n**연관 링크:** "
        "[https://edm.example/report/1](https://edm.example/report/1)"
    )
    assert "###" not in result
    assert "**" not in result
    assert "연관 링크: https://edm.example/report/1" in result
    assert "https://edm.example/report/1: https://edm.example/report/1" not in result


def test_markdown_links_are_clickable_in_html_mail():
    result = markdown_to_html(
        "- **연관 링크:** "
        "[https://edm.example/report/1](https://edm.example/report/1)"
    )

    assert "<ul>" in result
    assert "<strong>연관 링크:</strong>" in result
    assert (
        '<a href="https://edm.example/report/1">'
        "https://edm.example/report/1</a>"
    ) in result


def test_html_mail_escapes_untrusted_text():
    row = _row()
    row["request_title"] = "<script>alert(1)</script>"

    contents = build_html_contents(row)

    assert "<script>" not in contents
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in contents


def test_recipients_include_allowlisted_primary_and_knox_sender():
    recipients = build_recipients(_settings(), "tester@example.com")

    assert recipients == [
        {"emailAddress": "tester@example.com", "recipientType": "TO"},
        {"emailAddress": "agent@example.com", "recipientType": "TO"},
    ]


def test_recipients_remove_duplicate_sender_address_case_insensitively():
    settings = _settings(
        knox_mail_sender_email="Agent@Example.com",
        mail_allowed_recipients=("agent@example.com",),
    )

    recipients = build_recipients(settings, "agent@example.com")

    assert recipients == [
        {"emailAddress": "agent@example.com", "recipientType": "TO"}
    ]


def test_payload_includes_resolved_recipient_and_sender_copy():
    row = _row()
    payload = build_payload(_settings(), row, "tester@example.com")

    assert payload["subject"] == "[IFA Curator] A.N3 CA Middle Void Reference TEM"
    assert payload["recipients"] == [
        {"emailAddress": "tester@example.com", "recipientType": "TO"},
        {"emailAddress": "agent@example.com", "recipientType": "TO"},
    ]
    assert payload["sender"] == {"emailAddress": "agent@example.com"}
    assert "requester@example.com" not in str(payload["recipients"])


def test_html_payload_contains_clickable_related_link():
    payload = build_payload(
        _settings(knox_mail_content_type="HTML"),
        _row(),
        "tester@example.com",
    )

    assert payload["contentType"] == "HTML"
    assert (
        '<a href="https://edm.example/report/1">'
        "https://edm.example/report/1</a>"
    ) in payload["contents"]


def test_contents_include_answer_and_plain_url():
    contents = build_contents(_row())
    assert "A.N3 CA Middle Void Reference TEM" in contents
    assert "분석보고서 1" in contents
    assert "연관 링크: https://edm.example/report/1" in contents


def test_send_analysis_mail_uses_multipart_mail_field():
    settings = _settings(knox_mail_content_type="HTML")

    with patch(
        "request_pipeline.mail_sender.requests.post",
        return_value=_response(),
    ) as post:
        result = send_analysis_mail(settings, _row())

    assert result.mail_id == "mail-123"
    assert result.recipient == "tester@example.com"
    assert "json" not in post.call_args.kwargs
    assert "Content-Type" not in post.call_args.kwargs["headers"]
    assert post.call_args.kwargs["headers"]["System-ID"] == "KC123"

    mail_part = post.call_args.kwargs["files"]["mail"]
    assert mail_part[0] is None
    assert mail_part[2] == "application/json"

    payload = json.loads(mail_part[1])
    assert payload["contentType"] == "HTML"
    assert payload["recipients"] == [
        {"emailAddress": "tester@example.com", "recipientType": "TO"},
        {"emailAddress": "agent@example.com", "recipientType": "TO"},
    ]
    assert '<a href="https://edm.example/report/1">' in payload["contents"]


def test_disallowed_recipient_does_not_call_knox_api():
    settings = _settings(mail_allowed_recipients=("other@example.com",))

    with patch("request_pipeline.mail_sender.requests.post") as post:
        with pytest.raises(RuntimeError, match="not allowed"):
            send_analysis_mail(settings, _row())

    post.assert_not_called()


def test_timeout_is_marked_as_unknown_result():
    with patch(
        "request_pipeline.mail_sender.requests.post",
        side_effect=requests.Timeout("read timeout"),
    ):
        with pytest.raises(MailSendUnknownError):
            send_analysis_mail(_settings(), _row())
