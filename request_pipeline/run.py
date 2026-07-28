import logging

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


def _analyze_row(row: dict) -> None:
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
    except Exception as exc:
        db.mark_retry(settings, request_id, str(exc))
        logger.exception("analysis failed request_id=%s", request_id)


def retry_failed_analysis() -> None:
    for row in db.list_retry(settings):
        _analyze_row(row)


def collect_and_process_new_mail() -> None:
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

        _analyze_row({
            "id": request_id,
            "requester_user_id": parsed.requester_user_id,
            "sender_email": parsed.sender_email,
            "request_title": parsed.request_title,
            "mail_body": parsed.mail_body,
        })


def process_pending_send() -> None:
    # 메일 발송 API는 아직 연결하지 않는다.
    # MAIL_SEND_ENABLED=false 상태에서는 COMPLETED 건이 SEND_BLOCKED로 저장된다.
    if settings.mail_send_enabled:
        logger.warning("MAIL_SEND_ENABLED=true지만 발송 모듈은 아직 구현되지 않았습니다.")


def main() -> None:
    settings.validate()
    db.ensure_schema(settings)
    retry_failed_analysis()
    process_pending_send()
    collect_and_process_new_mail()


if __name__ == "__main__":
    main()
