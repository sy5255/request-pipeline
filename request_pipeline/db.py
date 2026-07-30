import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

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


@contextmanager
def pipeline_lock(settings: Settings) -> Iterator[bool]:
    """MySQL advisory lock으로 스케줄러 중복 실행을 방지합니다."""
    conn = connect(settings)
    cur = conn.cursor()
    acquired = False

    try:
        cur.execute(
            "SELECT GET_LOCK(%s, %s)",
            (settings.pipeline_lock_name, settings.pipeline_lock_wait_seconds),
        )
        row = cur.fetchone()
        acquired = bool(row and row[0] == 1)
        yield acquired
    finally:
        if acquired:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (settings.pipeline_lock_name,))
                cur.fetchone()
            except Exception:
                pass
        cur.close()
        conn.close()


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
                uidl,
                mail.message_id,
                mail.original_subject,
                mail.request_title,
                mail.normalized_subject,
                mail.subject_hash,
                mail.mail_body,
                mail.sender_email,
                mail.requester_user_id,
                mail.reply_to_email,
                mail.reply_to_email or mail.sender_email,
                mail.mail_sent_at,
            ),
        )
        request_id = int(cur.lastrowid)
        conn.commit()
        return request_id
    finally:
        cur.close()
        conn.close()


def find_duplicate(
    settings: Settings,
    request_id: int,
    subject_hash: str,
    sent_at: datetime | None,
) -> int | None:
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


def mark_ignored(settings: Settings, request_id: int) -> None:
    _update(
        settings,
        request_id,
        status="IGNORED",
        send_status="NOT_READY",
        last_error=None,
    )


def mark_duplicate(settings: Settings, request_id: int, duplicate_of: int) -> None:
    _update(
        settings,
        request_id,
        status="DUPLICATE",
        duplicate_of=duplicate_of,
        send_status="NOT_READY",
    )


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
            UPDATE request_mail
            SET status=IF(retry_count+1 >= %s,'FAILED','RETRY'),
                retry_count=retry_count+1,
                last_error=%s
            WHERE id=%s
            """,
            (settings.max_retry_count, error[:4000], request_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def recover_incomplete_requests(settings: Settings) -> dict[str, int]:
    """
    비정상 종료로 남은 대상 RECEIVED와 오래된 PROCESSING 요청을 RETRY로 복구합니다.

    복구 자체는 실패 횟수로 계산하지 않습니다. 실제 API 재호출이 실패했을 때만
    mark_retry()가 retry_count를 증가시킵니다.
    """
    conn = connect(settings)
    cur = conn.cursor()

    try:
        cur.execute(
            """
            UPDATE request_mail
            SET status='RETRY',
                last_error='Recovered target request left in RECEIVED state'
            WHERE status='RECEIVED'
              AND request_title IS NOT NULL
              AND retry_count < %s
            """,
            (settings.max_retry_count,),
        )
        received_count = cur.rowcount

        cur.execute(
            """
            UPDATE request_mail
            SET status='RETRY',
                last_error='Recovered stale PROCESSING request after scheduler interruption'
            WHERE status='PROCESSING'
              AND request_title IS NOT NULL
              AND updated_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
              AND retry_count < %s
            """,
            (settings.stale_processing_minutes, settings.max_retry_count),
        )
        processing_count = cur.rowcount

        # 과거 버전에서 비대상 메일이 RECEIVED로 남았던 데이터도 정리합니다.
        cur.execute(
            """
            UPDATE request_mail
            SET status='IGNORED',
                send_status='NOT_READY',
                last_error=NULL
            WHERE status='RECEIVED'
              AND request_title IS NULL
            """
        )
        ignored_count = cur.rowcount

        conn.commit()
        return {
            "received": received_count,
            "processing": processing_count,
            "ignored": ignored_count,
        }
    finally:
        cur.close()
        conn.close()


def list_retry(settings: Settings) -> list[dict[str, Any]]:
    conn = connect(settings)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT *
            FROM request_mail
            WHERE status='RETRY'
              AND request_title IS NOT NULL
              AND retry_count < %s
            ORDER BY id
            """,
            (settings.max_retry_count,),
        )
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
            f"UPDATE request_mail SET {', '.join(f'{key}=%s' for key in keys)} WHERE id=%s",
            tuple(fields[key] for key in keys) + (request_id,),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
