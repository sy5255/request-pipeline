import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime


@dataclass(frozen=True)
class ParsedMail:
    message_id: str | None
    original_subject: str
    request_title: str | None
    normalized_subject: str | None
    subject_hash: str | None
    mail_body: str
    sender_email: str | None
    reply_to_email: str | None
    requester_user_id: str | None
    mail_sent_at: datetime | None


def _decode_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            text = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain_parts if content_type == "text/plain" else html_parts).append(str(text))
    return "\n".join(plain_parts or html_parts).strip()


def normalize_subject(subject: str, prefix: str) -> tuple[str | None, str | None, str | None]:
    if not subject.startswith(prefix):
        return None, None, None
    request_title = subject[len(prefix):].strip()
    normalized = unicodedata.normalize("NFKC", request_title)
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    if not normalized:
        return request_title, "", hashlib.sha256(b"").hexdigest()
    return request_title, normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_mail(raw_message: bytes, prefix: str) -> ParsedMail:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    subject = str(message.get("Subject") or "").strip()
    request_title, normalized_subject, subject_hash = normalize_subject(subject, prefix)
    sender_email = parseaddr(str(message.get("From") or ""))[1] or None
    reply_to_email = parseaddr(str(message.get("Reply-To") or ""))[1] or None
    mail_sent_at = None
    if message.get("Date"):
        try:
            mail_sent_at = parsedate_to_datetime(str(message.get("Date"))).replace(tzinfo=None)
        except Exception:
            pass
    return ParsedMail(
        message_id=str(message.get("Message-ID") or "").strip() or None,
        original_subject=subject,
        request_title=request_title,
        normalized_subject=normalized_subject,
        subject_hash=subject_hash,
        mail_body=_decode_body(message),
        sender_email=sender_email,
        reply_to_email=reply_to_email,
        requester_user_id=(sender_email.split("@", 1)[0] if sender_email else None),
        mail_sent_at=mail_sent_at,
    )
