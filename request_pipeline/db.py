import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import mysql.connector

from request_pipeline.config import Settings
from request_pipeline.mail_parser import ParsedMail


def connect(settings: Settings):
    return mysql.connector.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
        user=settings.mysql_user,
        password=settings.mysql_password,
        autocommit=False,
    )


def ensure_schema(settings: Settings) -> None:
    sql = Path(__file__).resolve().parent.parent.joinpath("schema.sql").read_text(encoding="utf-8")
    conn = connect(settings)
    cur = conn.cursor()
    try:
        for statement in [s.strip() for s in sql.split(";") if s.strip()]:
            cur.execute(statement)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def uidl_exists(settings: Settings, uidl: str) -> bool:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM request_mail WHERE uidl=%s LIMIT 1", (uidl,))
        return cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()


def insert_received(settings: Settings, uidl: str, mail: ParsedMail) -> int:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO request_mail(
                uidl, message_id, original_subject, request_title, normalized_subject,
                subject_hash, mail_body, sender_email, requester_user_id, reply_to_email,
                original_recipient_email, mail_sent_at, status, send_status
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'RECEIVED','NOT_READY')
            """,
            (
                uidl, mail.message_id, mail.original_subject, mail.request_title,
                mail.normalized_subject, mail.subject_hash, mail.mail_body,
                mail.sender_email, mail.requester_user_id, mail.reply_to_email,
                mail.reply_to_email or mail.sender_email, mail.mail_sent_at,
            ),
        )
        request_id = int(cur.lastrowid)
        conn.commit()
        return request_id
    finally:
        cur.close()
        conn.close()


def find_duplicate(settings: Settings, request_id: int, subject_hash: str, sent_at: datetime | None) -> int | None:
    reference_time = sent_at or datetime.now()
    lower_bound = reference_time - timedelta(hours=settings.duplicate_window_hours)
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id FROM request_mail
            WHERE id<>%s AND subject_hash=%s
              AND status IN ('PROCESSING','COMPLETED')
              AND COALESCE(mail_sent_at, received_at) BETWEEN %s AND %s
            ORDER BY COALESCE(mail_sent_at, received_at) ASC LIMIT 1
            """,
            (request_id, subject_hash, lower_bound, reference_time),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        cur.close()
        conn.close()


def mark_duplicate(settings: Settings, request_id: int, duplicate_of: int) -> None:
    _update(settings, request_id, status="DUPLICATE", duplicate_of=duplicate_of, send_status="NOT_READY")


def mark_processing(settings: Settings, request_id: int) -> None:
    _update(settings, request_id, status="PROCESSING", last_error=None)


def mark_completed(settings: Settings, request_id: int, result: dict[str, Any]) -> None:
    trace = result.get("trace") or {}
    _update(
        settings,
        request_id,
        status="COMPLETED",
        answer_text=result.get("answer_text"),
        search_results_json=json.dumps(result.get("search_results") or [], ensure_ascii=False),
        chat_session_id=trace.get("session_id"),
        chat_turn_artifact_id=trace.get("turn_artifact_id"),
        chat_search_log_id=trace.get("search_log_id"),
        send_status="SEND_BLOCKED" if not settings.mail_send_enabled else "SEND_PENDING",
        last_error=None,
    )


def mark_retry(settings: Settings, request_id: int, error: str) -> None:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE request_mail SET status=IF(retry_count+1 >= %s,'FAILED','RETRY'),
                retry_count=retry_count+1, last_error=%s WHERE id=%s
            """,
            (settings.max_retry_count, error[:4000], request_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def list_retry(settings: Settings) -> list[dict[str, Any]]:
    conn = connect(settings)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM request_mail WHERE status='RETRY' AND retry_count < %s ORDER BY id", (settings.max_retry_count,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def _update(settings: Settings, request_id: int, **fields: Any) -> None:
    keys = list(fields)
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE request_mail SET {', '.join(f'{k}=%s' for k in keys)} WHERE id=%s",
            tuple(fields[k] for k in keys) + (request_id,),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
