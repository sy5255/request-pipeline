import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import mysql.connector

from request_pipeline.config import Settings
from request_pipeline.mail_parser import ParsedMail

MAIL_TABLE = "ae_llm_agent_mail"
RULE_TABLE = "ae_llm_agent_mail_rule"
API_PROFILE_TABLE = "ae_llm_agent_api_profile"
LEGACY_MAIL_TABLE = "request_mail"

DEFAULT_DEFECT_INSTRUCTION_TEMPLATE = (
    "아래 텍스트는 새로 들어온 불량분석 의뢰제목이야. "
    "이전에 이와 비슷한 분석 이력이 있는지 검색하고, "
    "참고할만한 이전 분석 이력을 찾아줘. "
    "참고할만한 이전 분석 레포트는 정확한 문서 이름을 함께 알려줘."
    "\n\n불량분석 의뢰제목:\n{{raw_request_title}}"
)


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


def _existing_mail_tables(settings: Settings) -> set[str]:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema=%s
              AND table_name IN (%s, %s)
            """,
            (settings.mysql_database, MAIL_TABLE, LEGACY_MAIL_TABLE),
        )
        return {str(row[0]) for row in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


def migrate_legacy_table(settings: Settings) -> bool:
    existing = _existing_mail_tables(settings)
    if MAIL_TABLE in existing and LEGACY_MAIL_TABLE in existing:
        raise RuntimeError(
            f"Both {LEGACY_MAIL_TABLE} and {MAIL_TABLE} exist. "
            "Reconcile the tables manually before running the pipeline."
        )
    if LEGACY_MAIL_TABLE not in existing:
        return False
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(f"RENAME TABLE `{LEGACY_MAIL_TABLE}` TO `{MAIL_TABLE}`")
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def _column_exists(settings: Settings, table_name: str, column_name: str) -> bool:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s AND column_name=%s
            LIMIT 1
            """,
            (settings.mysql_database, table_name, column_name),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()


def _index_exists(settings: Settings, table_name: str, index_name: str) -> bool:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM information_schema.statistics
            WHERE table_schema=%s AND table_name=%s AND index_name=%s
            LIMIT 1
            """,
            (settings.mysql_database, table_name, index_name),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()


def _ensure_mail_route_columns(settings: Settings) -> None:
    column_definitions = {
        "source_type": "VARCHAR(30) NOT NULL DEFAULT 'POP3' AFTER `uidl`",
        "mailbox_key": "VARCHAR(255) NOT NULL DEFAULT 'default' AFTER `source_type`",
        "raw_hash": "CHAR(64) NULL AFTER `subject_hash`",
        "route_type": "VARCHAR(30) NOT NULL DEFAULT 'UNCLASSIFIED' AFTER `duplicate_of`",
        "route_case": "VARCHAR(100) NULL AFTER `route_type`",
        "route_rule_id": "BIGINT NULL AFTER `route_case`",
        "route_rule_key": "VARCHAR(100) NULL AFTER `route_rule_id`",
        "route_rule_version": "INT NULL AFTER `route_rule_key`",
        "route_reason": "TEXT NULL AFTER `route_rule_version`",
        "route_matches_json": "JSON NULL AFTER `route_reason`",
        "route_action_json": "JSON NULL AFTER `route_matches_json`",
        "classified_at": "DATETIME NULL AFTER `route_action_json`",
        "sharedworkspace_path": "VARCHAR(2000) NULL AFTER `classified_at`",
        "attachment_count": "INT NULL AFTER `sharedworkspace_path`",
        "saved_at": "DATETIME NULL AFTER `attachment_count`",
    }
    conn = connect(settings)
    cur = conn.cursor()
    try:
        for column_name, definition in column_definitions.items():
            if not _column_exists(settings, MAIL_TABLE, column_name):
                cur.execute(
                    f"ALTER TABLE `{MAIL_TABLE}` ADD COLUMN `{column_name}` {definition}"
                )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    indexes = {
        "idx_ae_llm_agent_mail_route_status": "(`route_type`, `status`)",
        "idx_ae_llm_agent_mail_rule": "(`route_rule_id`)",
        "idx_ae_llm_agent_mail_raw_hash": "(`raw_hash`)",
    }
    conn = connect(settings)
    cur = conn.cursor()
    try:
        for index_name, expression in indexes.items():
            if not _index_exists(settings, MAIL_TABLE, index_name):
                cur.execute(
                    f"ALTER TABLE `{MAIL_TABLE}` ADD INDEX `{index_name}` {expression}"
                )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _ensure_api_profile_columns(settings: Settings) -> None:
    if _column_exists(settings, API_PROFILE_TABLE, "instruction_template"):
        return
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            ALTER TABLE `{API_PROFILE_TABLE}`
            ADD COLUMN `instruction_template` LONGTEXT NULL
            AFTER `response_config_json`
            """
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _seed_default_instruction_template(settings: Settings) -> int:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{API_PROFILE_TABLE}`
            SET instruction_template=%s
            WHERE profile_key='defect-analysis'
              AND (instruction_template IS NULL OR TRIM(instruction_template)='')
            """,
            (DEFAULT_DEFECT_INSTRUCTION_TEMPLATE,),
        )
        count = cur.rowcount
        conn.commit()
        return count
    finally:
        cur.close()
        conn.close()


def _backfill_legacy_api_routes(settings: Settings) -> int:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{MAIL_TABLE}`
            SET route_type='API_ANALYSIS',
                route_case='DEFECT_ANALYSIS_REQUEST',
                route_rule_key='api_defect_analysis_v1',
                route_rule_version=1,
                route_action_json=JSON_OBJECT('api_profile','defect-analysis'),
                route_reason='Backfilled from legacy request-pipeline record',
                classified_at=COALESCE(classified_at, created_at)
            WHERE route_type='UNCLASSIFIED'
              AND request_title IS NOT NULL
            """
        )
        count = cur.rowcount
        conn.commit()
        return count
    finally:
        cur.close()
        conn.close()


def ensure_schema(settings: Settings) -> dict[str, int]:
    sql = Path(__file__).resolve().parent.parent.joinpath("schema.sql").read_text(
        encoding="utf-8"
    )
    conn = connect(settings)
    cur = conn.cursor()
    try:
        for statement in [item.strip() for item in sql.split(";") if item.strip()]:
            cur.execute(statement)
        conn.commit()
    finally:
        cur.close()
        conn.close()

    _ensure_mail_route_columns(settings)
    _ensure_api_profile_columns(settings)
    seeded_instruction_templates = _seed_default_instruction_template(settings)
    backfilled = _backfill_legacy_api_routes(settings)
    return {
        "backfilled_api_routes": backfilled,
        "seeded_instruction_templates": seeded_instruction_templates,
    }


def _decode_json_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    for field in fields:
        value = row.get(field)
        if isinstance(value, str):
            row[field] = json.loads(value)
    return row


def list_enabled_rules(settings: Settings) -> list[dict[str, Any]]:
    conn = connect(settings)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"SELECT * FROM `{RULE_TABLE}` WHERE enabled=1 ORDER BY priority, id"
        )
        return [
            _decode_json_fields(row, ("match_config_json", "action_config_json"))
            for row in cur.fetchall()
        ]
    finally:
        cur.close()
        conn.close()


def get_api_profile(settings: Settings, profile_key: str) -> dict[str, Any]:
    conn = connect(settings)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT * FROM `{API_PROFILE_TABLE}`
            WHERE profile_key=%s AND enabled=1
            LIMIT 1
            """,
            (profile_key,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Enabled API profile not found: {profile_key}")
        return _decode_json_fields(
            row,
            ("headers_json", "request_template_json", "response_config_json"),
        )
    finally:
        cur.close()
        conn.close()


def uidl_exists(settings: Settings, uidl: str) -> bool:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT 1 FROM `{MAIL_TABLE}` WHERE uidl=%s LIMIT 1", (uidl,))
        return cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()


def insert_received(
    settings: Settings,
    uidl: str,
    mail: ParsedMail,
    *,
    route_type: str = "API_ANALYSIS",
    route_case: str = "DEFECT_ANALYSIS_REQUEST",
    route_rule_key: str = "api_defect_analysis_v1",
) -> int:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            INSERT INTO `{MAIL_TABLE}`(
                uidl, source_type, mailbox_key, message_id, original_subject,
                request_title, normalized_subject, subject_hash, mail_body,
                sender_email, requester_user_id, reply_to_email,
                original_recipient_email, mail_sent_at,
                route_type, route_case, route_rule_key, route_rule_version,
                route_action_json, route_reason, classified_at, status, send_status
            ) VALUES(
                %s,'POP3',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,1,JSON_OBJECT('api_profile','defect-analysis'),
                'Legacy request-pipeline POP3 classification',NOW(),'ROUTED','NOT_READY'
            )
            """,
            (
                uidl,
                settings.pop3_user or "default",
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
                route_type,
                route_case,
                route_rule_key,
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
            f"""
            SELECT id FROM `{MAIL_TABLE}`
            WHERE id<>%s AND subject_hash=%s
              AND route_type='API_ANALYSIS'
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
    _update(settings, request_id, route_type="IGNORE", status="IGNORED", send_status="NOT_READY", last_error=None)


def mark_duplicate(settings: Settings, request_id: int, duplicate_of: int) -> None:
    _update(settings, request_id, status="DUPLICATE", duplicate_of=duplicate_of, send_status="NOT_READY")


def claim_api_request(settings: Settings, request_id: int) -> bool:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{MAIL_TABLE}` SET status='PROCESSING', last_error=NULL
            WHERE id=%s AND route_type='API_ANALYSIS'
              AND status IN ('ROUTED','RETRY')
            """,
            (request_id,),
        )
        claimed = cur.rowcount == 1
        conn.commit()
        return claimed
    finally:
        cur.close()
        conn.close()


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
            f"""
            UPDATE `{MAIL_TABLE}`
            SET status=IF(retry_count+1 >= %s,'FAILED','RETRY'),
                retry_count=retry_count+1, last_error=%s
            WHERE id=%s AND route_type='API_ANALYSIS'
            """,
            (settings.max_retry_count, error[:4000], request_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def recover_incomplete_requests(settings: Settings) -> dict[str, int]:
    conn = connect(settings)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE `{MAIL_TABLE}`
            SET status='RETRY',
                last_error='Recovered stale API PROCESSING request after scheduler interruption'
            WHERE route_type='API_ANALYSIS' AND status='PROCESSING'
              AND updated_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
              AND retry_count < %s
            """,
            (settings.stale_processing_minutes, settings.max_retry_count),
        )
        processing_count = cur.rowcount
        conn.commit()
        return {"processing": processing_count}
    finally:
        cur.close()
        conn.close()


def list_api_ready(settings: Settings, limit: int) -> list[dict[str, Any]]:
    conn = connect(settings)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT * FROM `{MAIL_TABLE}`
            WHERE route_type='API_ANALYSIS'
              AND status IN ('ROUTED','RETRY')
              AND retry_count < %s
            ORDER BY CASE WHEN status='RETRY' THEN 0 ELSE 1 END, id
            LIMIT %s
            """,
            (settings.max_retry_count, limit),
        )
        rows = cur.fetchall()
        return [
            _decode_json_fields(row, ("route_action_json", "route_matches_json"))
            for row in rows
        ]
    finally:
        cur.close()
        conn.close()


def _update(settings: Settings, request_id: int, **fields: Any) -> None:
    keys = list(fields)
    conn = connect(settings)
    cur = conn.cursor()
    try:
        assignments = ", ".join(f"`{key}`=%s" for key in keys)
        cur.execute(
            f"UPDATE `{MAIL_TABLE}` SET {assignments} WHERE id=%s",
            tuple(fields[key] for key in keys) + (request_id,),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
