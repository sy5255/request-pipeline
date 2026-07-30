import logging
import time

from request_pipeline import db
from request_pipeline.config import settings
from request_pipeline.mail_client import iter_messages
from request_pipeline.mail_parser import parse_mail
from request_pipeline.web_client import analyze_request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("request_pipeline")


def _analyze_row(row: dict) -> bool:
    """API_ANALYSIS 경로 요청 한 건을 원자적으로 선점한 뒤 분석합니다."""
    request_id = int(row["id"])

    if not db.claim_api_request(settings, request_id):
        logger.info(
            "analysis claim skipped request_id=%s route_type=%s status=%s",
            request_id,
            row.get("route_type"),
            row.get("status"),
        )
        return True

    try:
        result = analyze_request(
            settings,
            request_id=request_id,
            requester_user_id=row["requester_user_id"],
            requester_email=row["sender_email"],
            request_title=row["request_title"],
            mail_body=row.get("mail_body") or "",
        )
        db.mark_completed(settings, request_id, result)
        logger.info(
            "analysis completed request_id=%s route_case=%s rule=%s",
            request_id,
            row.get("route_case"),
            row.get("route_rule_key"),
        )
        return True
    except Exception as exc:
        db.mark_retry(settings, request_id, str(exc))
        logger.exception("analysis failed request_id=%s", request_id)
        return False


def _sleep_between_requests() -> None:
    if settings.analysis_interval_seconds > 0:
        time.sleep(settings.analysis_interval_seconds)


def process_api_queue(limit: int) -> tuple[int, bool]:
    rows = db.list_api_ready(settings, limit)
    logger.info("api queue ready=%s processing_limit=%s", len(rows), limit)

    processed_count = 0
    for index, row in enumerate(rows):
        if not _analyze_row(row):
            logger.warning(
                "api queue processing stopped after failure request_id=%s",
                row["id"],
            )
            return processed_count, False

        processed_count += 1
        if index < len(rows) - 1:
            _sleep_between_requests()

    return processed_count, True


def collect_legacy_pop3_mail() -> int:
    """
    중앙 ingest_pop3 collector 전환 전의 임시 호환 모드입니다.

    POP3_COLLECTION_ENABLED=false가 기본이며, 활성화하더라도 API 대상 접두어만
    API_ANALYSIS / ROUTED 상태로 등록합니다.
    """
    if not settings.pop3_collection_enabled:
        return 0

    collected = 0
    for raw_mail in iter_messages(settings):
        if db.uidl_exists(settings, raw_mail.uidl):
            continue

        parsed = parse_mail(raw_mail.raw_bytes, settings.target_subject_prefix)
        if parsed.request_title is None:
            logger.info("legacy collector skipped non-api mail uidl=%s", raw_mail.uidl)
            continue

        request_id = db.insert_received(settings, raw_mail.uidl, parsed)
        collected += 1
        logger.info("legacy API mail routed request_id=%s", request_id)

    return collected


def process_pending_send() -> None:
    if settings.mail_send_enabled:
        logger.warning("MAIL_SEND_ENABLED=true지만 발송 모듈은 아직 구현되지 않았습니다.")


def _run_once() -> None:
    recovery = db.recover_incomplete_requests(settings)
    if any(recovery.values()):
        logger.warning(
            "recovered incomplete API requests processing=%s",
            recovery["processing"],
        )

    legacy_collected = collect_legacy_pop3_mail()
    processed_count, succeeded = process_api_queue(settings.max_analysis_per_run)
    process_pending_send()

    if not succeeded:
        logger.warning("pipeline stopped because an API request failed")
        return

    logger.info(
        "pipeline finished legacy_collected=%s api_completed=%s max_per_run=%s",
        legacy_collected,
        processed_count,
        settings.max_analysis_per_run,
    )


def main() -> None:
    settings.validate()

    with db.pipeline_lock(settings) as acquired:
        if not acquired:
            logger.warning(
                "another pipeline run is active; current run skipped lock=%s",
                settings.pipeline_lock_name,
            )
            return

        logger.info("pipeline lock acquired lock=%s", settings.pipeline_lock_name)

        if db.migrate_legacy_table(settings):
            logger.warning(
                "legacy table migrated old=%s new=%s",
                db.LEGACY_MAIL_TABLE,
                db.MAIL_TABLE,
            )

        schema_result = db.ensure_schema(settings)
        if schema_result["backfilled_api_routes"]:
            logger.warning(
                "legacy API routes backfilled count=%s",
                schema_result["backfilled_api_routes"],
            )

        _run_once()


if __name__ == "__main__":
    main()
