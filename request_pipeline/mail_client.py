import poplib
from dataclasses import dataclass
from typing import Iterator

from request_pipeline.config import Settings


@dataclass(frozen=True)
class RawMail:
    uidl: str
    raw_bytes: bytes


def iter_messages(settings: Settings) -> Iterator[RawMail]:
    client_cls = poplib.POP3_SSL if settings.pop3_use_ssl else poplib.POP3
    client = client_cls(
        settings.pop3_host,
        settings.pop3_port,
        timeout=settings.pop3_timeout_seconds,
    )
    try:
        client.user(settings.pop3_user)
        client.pass_(settings.pop3_password)
        _, uidl_lines, _ = client.uidl()
        for line in uidl_lines:
            parts = line.decode("utf-8", errors="replace").split(maxsplit=1)
            if len(parts) != 2:
                continue
            message_number, uidl = parts
            _, lines, _ = client.retr(int(message_number))
            yield RawMail(uidl=uidl, raw_bytes=b"\r\n".join(lines) + b"\r\n")
    finally:
        try:
            client.quit()
        except Exception:
            client.close()
