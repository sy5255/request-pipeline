import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from request_pipeline.config import Settings


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


class MailSendError(RuntimeError):
    """Knox API가 명확한 실패 응답을 반환한 경우입니다."""


class MailSendUnknownError(RuntimeError):
    """요청 결과가 불명확해 자동 재시도가 위험한 경우입니다."""


@dataclass(frozen=True)
class MailSendResult:
    mail_id: str
    response_json: dict[str, Any]
    response_text: str
    status_code: int
    recipient: str


def _valid_email(value: str) -> str:
    email = str(value or "").strip()
    if not _EMAIL_RE.match(email):
        raise RuntimeError(f"Invalid email address: {email or '<empty>'}")
    return email


def resolve_recipient(settings: Settings, row: dict[str, Any]) -> str:
    """TEST 모드는 모든 메일을 단일 테스트 주소로 강제 우회합니다."""
    mode = settings.mail_recipient_mode
    if mode == "TEST":
        return _valid_email(settings.mail_test_recipient)

    if mode != "ORIGINAL":
        raise RuntimeError(f"Unsupported MAIL_RECIPIENT_MODE: {mode}")
    if not settings.mail_allow_original_recipient:
        raise RuntimeError(
            "Original recipient delivery is blocked. "
            "Set MAIL_ALLOW_ORIGINAL_RECIPIENT=true explicitly."
        )

    recipient = (
        row.get("reply_to_email")
        or row.get("sender_email")
        or row.get("original_recipient_email")
    )
    return _valid_email(str(recipient or ""))


def build_subject(settings: Settings, row: dict[str, Any]) -> str:
    source = str(
        row.get("request_title")
        or row.get("original_subject")
        or f"요청 {row.get('id')}"
    ).strip()
    subject = f"{settings.mail_subject_prefix} {source}".strip()
    return subject[:200]


def markdown_to_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n")
    text = _MARKDOWN_LINK_RE.sub(lambda m: f"{m.group(1)}: {m.group(2)}", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_contents(row: dict[str, Any]) -> str:
    request_title = str(
        row.get("request_title") or row.get("original_subject") or ""
    ).strip()
    answer = markdown_to_text(str(row.get("answer_text") or ""))
    if not answer:
        raise RuntimeError(f"answer_text is empty for request_id={row.get('id')}")

    return (
        "안녕하세요.\n\n"
        "요청하신 불량분석 의뢰 제목을 기준으로 유사한 이전 분석 이력을 검색했습니다.\n\n"
        f"의뢰 제목\n{request_title}\n\n"
        f"{answer}\n\n"
        "본 결과는 현재 시스템에서 검색 가능한 문서를 기준으로 생성되었습니다."
    ).strip()


def build_recipients(
    settings: Settings,
    primary_recipient: str,
) -> list[dict[str, str]]:
    """주 수신자와 Knox 발송 계정을 TO로 포함하고 중복 주소를 제거합니다."""
    addresses = [
        _valid_email(primary_recipient),
        _valid_email(settings.knox_mail_sender_email),
    ]

    recipients: list[dict[str, str]] = []
    seen: set[str] = set()
    for address in addresses:
        normalized = address.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        recipients.append(
            {
                "emailAddress": address,
                "recipientType": "TO",
            }
        )

    return recipients


def build_payload(
    settings: Settings,
    row: dict[str, Any],
    recipient: str,
) -> dict[str, Any]:
    sender_email = _valid_email(settings.knox_mail_sender_email)
    return {
        "subject": build_subject(settings, row),
        "docSecuType": settings.knox_mail_doc_secu_type,
        "contents": build_contents(row),
        "contentType": settings.knox_mail_content_type,
        "sender": {"emailAddress": sender_email},
        "recipients": build_recipients(settings, recipient),
    }


def _mail_id_from_response(data: dict[str, Any], response: requests.Response) -> str:
    candidates = (
        data.get("mailId"),
        data.get("mail_id"),
        data.get("id"),
        (data.get("result") or {}).get("mailId")
        if isinstance(data.get("result"), dict)
        else None,
        response.headers.get("X-Mail-Id"),
        response.headers.get("Location"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value[:255]
    return ""


def send_analysis_mail(
    settings: Settings,
    row: dict[str, Any],
) -> MailSendResult:
    recipient = resolve_recipient(settings, row)
    payload = build_payload(settings, row, recipient)
    query = urlencode({"userId": settings.knox_mail_user_id})
    url = f"{settings.knox_mail_api_url}?{query}"
    headers = {
        "accept": "*/*",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.knox_mail_auth_token}",
        "System-ID": settings.knox_mail_system_id,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=(
                settings.knox_mail_connect_timeout,
                settings.knox_mail_read_timeout,
            ),
            verify=settings.knox_mail_verify,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise MailSendUnknownError(
            f"Knox mail request result is unknown: {exc}"
        ) from exc
    except requests.RequestException as exc:
        raise MailSendError(f"Knox mail request failed: {exc}") from exc

    response_text = response.text or ""
    try:
        response_json = response.json() if response_text else {}
    except (ValueError, json.JSONDecodeError):
        response_json = {"raw_text": response_text[:4000]}

    if not 200 <= response.status_code < 300:
        raise MailSendError(
            f"Knox mail API returned HTTP {response.status_code}: "
            f"{response_text[:2000]}"
        )

    return MailSendResult(
        mail_id=_mail_id_from_response(response_json, response),
        response_json=response_json,
        response_text=response_text,
        status_code=response.status_code,
        recipient=recipient,
    )
