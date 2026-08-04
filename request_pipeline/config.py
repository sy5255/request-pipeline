import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_database: str = os.getenv("MYSQL_DATABASE", "ifa")
    mysql_user: str = os.getenv("MYSQL_USER", "ifa_user")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")

    # 중앙 ingest_pop3 collector 도입 후 request-pipeline은 기본적으로 DB queue만 처리합니다.
    pop3_collection_enabled: bool = _bool("POP3_COLLECTION_ENABLED", False)
    pop3_host: str = os.getenv("POP3_HOST", "")
    pop3_port: int = int(os.getenv("POP3_PORT", "995"))
    pop3_user: str = os.getenv("POP3_USER", "")
    pop3_password: str = os.getenv("POP3_PASSWORD", "")
    pop3_use_ssl: bool = _bool("POP3_USE_SSL", True)
    pop3_timeout_seconds: int = int(os.getenv("POP3_TIMEOUT_SECONDS", "30"))

    # 레거시 POP3 수집 모드를 잠시 사용할 때만 쓰는 단일 API 대상 접두어입니다.
    target_subject_prefix: str = os.getenv(
        "TARGET_SUBJECT_PREFIX",
        "[분석 대기] [Defect 형태/성분 분석의뢰]",
    )
    duplicate_window_hours: int = int(os.getenv("DUPLICATE_WINDOW_HOURS", "72"))
    max_retry_count: int = int(os.getenv("MAX_RETRY_COUNT", "3"))

    max_analysis_per_run: int = int(os.getenv("MAX_ANALYSIS_PER_RUN", "3"))
    analysis_interval_seconds: float = float(os.getenv("ANALYSIS_INTERVAL_SECONDS", "3"))
    stale_processing_minutes: int = int(os.getenv("STALE_PROCESSING_MINUTES", "15"))

    pipeline_lock_name: str = os.getenv(
        "PIPELINE_LOCK_NAME", "request_pipeline_scheduler"
    ).strip()
    pipeline_lock_wait_seconds: int = int(os.getenv("PIPELINE_LOCK_WAIT_SECONDS", "0"))

    report_search_base_url: str = os.getenv(
        "REPORT_SEARCH_BASE_URL",
        "https://ae-llm-agent--fa-report-search-prod.cdep1.ss.net",
    ).rstrip("/")
    report_search_service_key: str = os.getenv(
        "REPORT_SEARCH_SERVICE_KEY", "dev-only-change-this-key"
    )
    web_api_connect_timeout: int = int(os.getenv("WEB_API_CONNECT_TIMEOUT", "10"))
    web_api_read_timeout: int = int(os.getenv("WEB_API_READ_TIMEOUT", "180"))

    # 기존 개발 환경과의 호환성을 위해 미설정 시 SSL 검증을 비활성화합니다.
    # 운영에서는 반드시 true 또는 사내 CA 번들 경로를 명시해야 합니다.
    report_search_verify_ssl: bool = _bool("REPORT_SEARCH_VERIFY_SSL", False)
    report_search_ca_bundle: str = os.getenv("REPORT_SEARCH_CA_BUNDLE", "").strip()

    # MAIL_SEND_ENABLED=false이면 분석 파이프라인은 계속 동작하고 메일 발송만 중단됩니다.
    mail_send_enabled: bool = _bool("MAIL_SEND_ENABLED", False)
    mail_recipient_mode: str = os.getenv("MAIL_RECIPIENT_MODE", "TEST").strip().upper()
    mail_test_recipient: str = os.getenv("MAIL_TEST_RECIPIENT", "").strip()
    mail_allow_original_recipient: bool = _bool("MAIL_ALLOW_ORIGINAL_RECIPIENT", False)
    mail_send_batch_size: int = int(os.getenv("MAIL_SEND_BATCH_SIZE", "1"))
    mail_send_stale_minutes: int = int(os.getenv("MAIL_SEND_STALE_MINUTES", "15"))
    mail_subject_prefix: str = os.getenv(
        "MAIL_SUBJECT_PREFIX", "[IFA Curator]"
    ).strip()

    knox_mail_api_url: str = os.getenv("KNOX_MAIL_API_URL", "").strip()
    knox_mail_user_id: str = os.getenv("KNOX_MAIL_USER_ID", "agent").strip()
    knox_mail_auth_token: str = os.getenv("KNOX_MAIL_AUTH_TOKEN", "").strip()
    knox_mail_system_id: str = os.getenv("KNOX_MAIL_SYSTEM_ID", "").strip()
    knox_mail_sender_email: str = os.getenv("KNOX_MAIL_SENDER_EMAIL", "").strip()
    knox_mail_doc_secu_type: str = os.getenv(
        "KNOX_MAIL_DOC_SECU_TYPE", "PERSONAL"
    ).strip().upper()
    knox_mail_content_type: str = os.getenv(
        "KNOX_MAIL_CONTENT_TYPE", "TEXT"
    ).strip().upper()
    knox_mail_connect_timeout: int = int(os.getenv("KNOX_MAIL_CONNECT_TIMEOUT", "10"))
    knox_mail_read_timeout: int = int(os.getenv("KNOX_MAIL_READ_TIMEOUT", "30"))
    knox_mail_verify_ssl: bool = _bool("KNOX_MAIL_VERIFY_SSL", True)
    knox_mail_ca_bundle: str = os.getenv("KNOX_MAIL_CA_BUNDLE", "").strip()

    @property
    def report_search_verify(self) -> bool | str:
        if self.report_search_ca_bundle:
            return self.report_search_ca_bundle
        return self.report_search_verify_ssl

    @property
    def knox_mail_verify(self) -> bool | str:
        if self.knox_mail_ca_bundle:
            return self.knox_mail_ca_bundle
        return self.knox_mail_verify_ssl

    def validate(self) -> None:
        required = {
            "MYSQL_PASSWORD": self.mysql_password,
        }

        # 구버전 Settings 인스턴스와 새 validate()가 섞인 배포에서도
        # AttributeError로 DB queue 전체가 중단되지 않도록 기본값을 False로 둡니다.
        pop3_collection_enabled = bool(
            getattr(self, "pop3_collection_enabled", False)
        )
        if pop3_collection_enabled:
            required.update(
                {
                    "POP3_HOST": getattr(self, "pop3_host", ""),
                    "POP3_USER": getattr(self, "pop3_user", ""),
                    "POP3_PASSWORD": getattr(self, "pop3_password", ""),
                }
            )

        if self.mail_send_enabled:
            required.update(
                {
                    "KNOX_MAIL_API_URL": self.knox_mail_api_url,
                    "KNOX_MAIL_AUTH_TOKEN": self.knox_mail_auth_token,
                    "KNOX_MAIL_SYSTEM_ID": self.knox_mail_system_id,
                    "KNOX_MAIL_SENDER_EMAIL": self.knox_mail_sender_email,
                }
            )
            if self.mail_recipient_mode == "TEST":
                required["MAIL_TEST_RECIPIENT"] = self.mail_test_recipient

        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required settings: {', '.join(missing)}")
        if self.max_analysis_per_run < 1:
            raise RuntimeError("MAX_ANALYSIS_PER_RUN must be at least 1")
        if self.analysis_interval_seconds < 0:
            raise RuntimeError("ANALYSIS_INTERVAL_SECONDS must be 0 or greater")
        if self.stale_processing_minutes < 1:
            raise RuntimeError("STALE_PROCESSING_MINUTES must be at least 1")
        if not self.pipeline_lock_name or len(self.pipeline_lock_name) > 64:
            raise RuntimeError("PIPELINE_LOCK_NAME must contain 1 to 64 characters")
        if self.pipeline_lock_wait_seconds < 0:
            raise RuntimeError("PIPELINE_LOCK_WAIT_SECONDS must be 0 or greater")
        if self.mail_recipient_mode not in {"TEST", "ORIGINAL"}:
            raise RuntimeError("MAIL_RECIPIENT_MODE must be TEST or ORIGINAL")
        if self.mail_send_enabled and self.mail_recipient_mode == "ORIGINAL":
            if not self.mail_allow_original_recipient:
                raise RuntimeError(
                    "MAIL_ALLOW_ORIGINAL_RECIPIENT=true is required for ORIGINAL mode"
                )
        if self.mail_send_batch_size < 1:
            raise RuntimeError("MAIL_SEND_BATCH_SIZE must be at least 1")
        if self.mail_send_stale_minutes < 1:
            raise RuntimeError("MAIL_SEND_STALE_MINUTES must be at least 1")
        if self.knox_mail_connect_timeout < 1 or self.knox_mail_read_timeout < 1:
            raise RuntimeError("Knox mail timeouts must be at least 1 second")
        if self.knox_mail_content_type != "TEXT":
            raise RuntimeError("KNOX_MAIL_CONTENT_TYPE currently supports TEXT only")


settings = Settings()
