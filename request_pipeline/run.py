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


def _sleep_between_requests() -> None:
    if settings.analysis_interval_seconds > 0:
        time.sleep(settings.analysis_interval_seconds)


def retry_failed_analysis(limit: int) -> tuple[int, bool]:
    """
    RETRY 건을 제한된 수만 처리합니다.

    반환값의 두 번째 값이 False이면 첫 실패가 발생한 것이므로
    이번 실행에서는 신규 메일 분석도 진행하지 않습니다.
    """
    rows = db.list_retry(settings)
    target_rows = rows[:limit]

    logger.info(
        "pending retry count=%s processing_limit=%s",
        len(rows),
        len(target_rows),
    )

    processed_count = 0
    for index, row in enumerate(target_rows):
        if not _analyze_row(row):
            logger.warning(
                "retry processing stopped after failure request_id=%s",
                row["id"],
            )
            return processed_count, False

        processed_count += 1
        if index < len(target_rows) - 1:
            _sleep_between_requests()

    return processed_count, True


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
            db.mark_ignored(settings, request_id)
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
            logger.info(
                "duplicate request_id=%s duplicate_of=%s",
                request_id,
                duplicate_of,
            )
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

        _sleep_between_requests()

    return analyzed_count


def process_pending_send() -> None:
    # 메일 발송 API는 아직 연결하지 않는다.
    # MAIL_SEND_ENABLED=false 상태에서는 COMPLETED 건이 SEND_BLOCKED로 저장된다.
    if settings.mail_send_enabled:
        logger.warning("MAIL_SEND_ENABLED=true지만 발송 모듈은 아직 구현되지 않았습니다.")


def _run_once() -> None:
    recovery = db.recover_incomplete_requests(settings)
    if any(recovery.values()):
        logger.warning(
            "recovered incomplete requests received=%s processing=%s ignored=%s",
            recovery["received"],
            recovery["processing"],
            recovery["ignored"],
        )

    retry_count, retry_succeeded = retry_failed_analysis(settings.max_analysis_per_run)
    process_pending_send()

    if not retry_succeeded:
        logger.warning("pipeline stopped because a retry request failed")
        return

    remaining_limit = settings.max_analysis_per_run - retry_count

    # RETRY 성공 직후 신규 API 호출이 이어질 때도 동일한 간격을 보장합니다.
    if retry_count > 0 and remaining_limit > 0:
        _sleep_between_requests()

    new_count = collect_and_process_new_mail(remaining_limit)

    logger.info(
        "pipeline finished retry_completed=%s new_completed=%s max_per_run=%s",
        retry_count,
        new_count,
        settings.max_analysis_per_run,
    )


def main() -> None:
    settings.validate()
    db.ensure_schema(settings)

    # MySQL advisory lock은 연결이 끊기면 자동 해제되므로 프로세스 강제 종료에도 안전합니다.
    with db.pipeline_lock(settings) as acquired:
        if not acquired:
            logger.warning(
                "another pipeline run is active; current run skipped lock=%s",
                settings.pipeline_lock_name,
            )
            return

        logger.info("pipeline lock acquired lock=%s", settings.pipeline_lock_name)
        _run_once()


if __name__ == "__main__":
    main()
