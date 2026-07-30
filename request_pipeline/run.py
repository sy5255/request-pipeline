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
    """요청 한 건을 분석하고 성공 여부를 반환합니다."""
    request_id = int(row["id"])
    try:
        db.mark_processing(settings, request_id)
        result = analyze_request(
            settings,
            request_id=request_id,
            requester_user_id=row["requester_user_id"],
            requester_email=row["sender_email"],
            request_title=row["request_title"],
            mail_body=row.get("mail_body") or "",
        )
        db.mark_completed(settings, request_id, result)
        logger.info("analysis completed request_id=%s", request_id)
        return True
    except Exception as exc:
        db.mark_retry(settings, request_id, str(exc))
        logger.exception("analysis failed request_id=%s", request_id)
        return False


def _wait_before_next_request(processed_count: int, limit: int) -> None:
    """다음 API 호출이 남아 있을 때만 호출 간격을 둡니다."""
    if processed_count < limit and settings.analysis_interval_seconds > 0:
        time.sleep(settings.analysis_interval_seconds)


def retry_failed_analysis(limit: int) -> int:
    """RETRY 건을 제한된 수만 처리하고, 첫 실패 시 이번 실행을 중단합니다."""
    rows = db.list_retry(settings)
    target_rows = rows[:limit]

    logger.info(
        "pending retry count=%s processing_limit=%s",
        len(rows),
        len(target_rows),
    )

    processed_count = 0
    for row in target_rows:
        if not _analyze_row(row):
            logger.warning(
                "retry processing stopped after failure request_id=%s",
                row["id"],
            )
            break

        processed_count += 1
        _wait_before_next_request(processed_count, len(target_rows))

    return processed_count


def collect_and_process_new_mail(limit: int) -> int:
    """신규 대상 메일을 제한된 수만 분석하고, 첫 실패 시 추가 호출을 중단합니다."""
    if limit <= 0:
        logger.info("new mail analysis skipped because run limit was reached")
        return 0

    analyzed_count = 0

    for raw_mail in iter_messages(settings):
        if db.uidl_exists(settings, raw_mail.uidl):
            continue

        parsed = parse_mail(raw_mail.raw_bytes, settings.target_subject_prefix)
        request_id = db.insert_received(settings, raw_mail.uidl, parsed)

        if parsed.request_title is None:
            logger.info("ignored non-target mail request_id=%s", request_id)
            continue

        if not parsed.sender_email or not parsed.requester_user_id:
            db.mark_retry(settings, request_id, "Sender email is missing")
            continue

        duplicate_of = db.find_duplicate(
            settings,
            request_id=request_id,
            subject_hash=parsed.subject_hash or "",
            sent_at=parsed.mail_sent_at,
        )
        if duplicate_of:
            db.mark_duplicate(settings, request_id, duplicate_of)
            logger.info("duplicate request_id=%s duplicate_of=%s", request_id, duplicate_of)
            continue

        success = _analyze_row(
            {
                "id": request_id,
                "requester_user_id": parsed.requester_user_id,
                "sender_email": parsed.sender_email,
                "request_title": parsed.request_title,
                "mail_body": parsed.mail_body,
            }
        )

        if not success:
            logger.warning(
                "new mail processing stopped after failure request_id=%s",
                request_id,
            )
            break

        analyzed_count += 1
        if analyzed_count >= limit:
            logger.info("new mail analysis limit reached max=%s", limit)
            break

        _wait_before_next_request(analyzed_count, limit)

    return analyzed_count


def process_pending_send() -> None:
    # 메일 발송 API는 아직 연결하지 않는다.
    # MAIL_SEND_ENABLED=false 상태에서는 COMPLETED 건이 SEND_BLOCKED로 저장된다.
    if settings.mail_send_enabled:
        logger.warning("MAIL_SEND_ENABLED=true지만 발송 모듈은 아직 구현되지 않았습니다.")


def main() -> None:
    settings.validate()
    db.ensure_schema(settings)

    retry_count = retry_failed_analysis(settings.max_analysis_per_run)
    remaining_limit = settings.max_analysis_per_run - retry_count

    process_pending_send()
    new_count = collect_and_process_new_mail(remaining_limit)

    logger.info(
        "pipeline finished retry_completed=%s new_completed=%s max_per_run=%s",
        retry_count,
        new_count,
        settings.max_analysis_per_run,
    )


if __name__ == "__main__":
    main()
