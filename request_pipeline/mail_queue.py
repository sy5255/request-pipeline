import json
from typing import Any

from request_pipeline import db
from request_pipeline.config import Settings


SEND_READY_STATUSES = ("SEND_BLOCKED", "SEND_PENDING")


def list_send_ready(settings: Settings, limit: int) -> list[dict[str, Any]]:
    conn = db.connect(settings)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT *
            FROM `{db.MAIL_TABLE}`
            WHERE route_type='API_ANALYSIS'
              AND status='COMPLETED'
              AND send_status IN ('SEND_BLOCKED','SEND_PENDING')
              AND answer_text IS NOT NULL
              AND TRIM(answer_text)<>''
            ORDER BY id
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall() or []
    finally:
        cur.close()
        conn.close()


def claim_send(settings: Settings, request_id: int) -> bool:
    conn = db.connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{db.MAIL_TABLE}`
            SET send_status='SENDING', last_error=NULL
            WHERE id=%s
              AND route_type='API_ANALYSIS'
              AND status='COMPLETED'
              AND send_status IN ('SEND_BLOCKED','SEND_PENDING')
              AND answer_text IS NOT NULL
              AND TRIM(answer_text)<>''
            """,
            (request_id,),
        )
        claimed = cur.rowcount == 1
        conn.commit()
        return claimed
    finally:
        cur.close()
        conn.close()


def mark_sent(
    settings: Settings,
    request_id: int,
    *,
    sent_mail_id: str,
    recipient: str,
    response_json: dict[str, Any],
) -> None:
    conn = db.connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{db.MAIL_TABLE}`
            SET send_status='SENT',
                sent_mail_id=%s,
                sent_at=NOW(),
                actual_recipient_email=%s,
                recipient_mode=%s,
                last_error=NULL,
                route_reason=CONCAT(
                    COALESCE(route_reason, ''),
                    %s
                )
            WHERE id=%s AND send_status='SENDING'
            """,
            (
                sent_mail_id or None,
                recipient,
                settings.mail_recipient_mode,
                "\n[MAIL_SEND_RESPONSE] "
                + json.dumps(response_json, ensure_ascii=False)[:3000],
                request_id,
            ),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def mark_send_failed(
    settings: Settings,
    request_id: int,
    error: str,
) -> None:
    conn = db.connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{db.MAIL_TABLE}`
            SET send_status='SEND_BLOCKED',
                last_error=%s
            WHERE id=%s AND send_status='SENDING'
            """,
            (error[:4000], request_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def mark_send_unknown(
    settings: Settings,
    request_id: int,
    error: str,
) -> None:
    conn = db.connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{db.MAIL_TABLE}`
            SET send_status='SEND_UNKNOWN',
                last_error=%s
            WHERE id=%s AND send_status='SENDING'
            """,
            (error[:4000], request_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def recover_stale_sending(settings: Settings) -> int:
    """중단된 SENDING은 중복 발송 위험 때문에 SEND_UNKNOWN으로 복구합니다."""
    conn = db.connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{db.MAIL_TABLE}`
            SET send_status='SEND_UNKNOWN',
                last_error='Recovered stale SENDING mail after scheduler interruption'
            WHERE send_status='SENDING'
              AND updated_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
            """,
            (settings.mail_send_stale_minutes,),
        )
        count = cur.rowcount
        conn.commit()
        return count
    finally:
        cur.close()
        conn.close()
