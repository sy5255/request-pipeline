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

    pop3_host: str = os.getenv("POP3_HOST", "")
    pop3_port: int = int(os.getenv("POP3_PORT", "995"))
    pop3_user: str = os.getenv("POP3_USER", "")
    pop3_password: str = os.getenv("POP3_PASSWORD", "")
    pop3_use_ssl: bool = _bool("POP3_USE_SSL", True)
    pop3_timeout_seconds: int = int(os.getenv("POP3_TIMEOUT_SECONDS", "30"))

    target_subject_prefix: str = os.getenv(
        "TARGET_SUBJECT_PREFIX",
        "[분석 대기] [Defect 형태/성분 분석의뢰]",
    )
    duplicate_window_hours: int = int(os.getenv("DUPLICATE_WINDOW_HOURS", "72"))
    max_retry_count: int = int(os.getenv("MAX_RETRY_COUNT", "3"))

    # RETRY와 신규 메일을 합쳐 한 번의 실행에서 호출할 최대 분석 건수입니다.
    max_analysis_per_run: int = int(os.getenv("MAX_ANALYSIS_PER_RUN", "3"))
    analysis_interval_seconds: float = float(os.getenv("ANALYSIS_INTERVAL_SECONDS", "3"))

    report_search_base_url: str = os.getenv(
        "REPORT_SEARCH_BASE_URL",
        "https://ae-llm-agent--fa-report-search-prod.cdep1.ss.net",
    ).rstrip("/")
    report_search_service_key: str = os.getenv(
        "REPORT_SEARCH_SERVICE_KEY", "dev-only-change-this-key"
    )
    web_api_connect_timeout: int = int(os.getenv("WEB_API_CONNECT_TIMEOUT", "10"))
    web_api_read_timeout: int = int(os.getenv("WEB_API_READ_TIMEOUT", "180"))

    # 운영에서는 SSL 검증을 유지하고 사내 CA 인증서 파일을 지정하는 것을 권장합니다.
    report_search_verify_ssl: bool = _bool("REPORT_SEARCH_VERIFY_SSL", True)
    report_search_ca_bundle: str = os.getenv("REPORT_SEARCH_CA_BUNDLE", "").strip()

    mail_send_enabled: bool = _bool("MAIL_SEND_ENABLED", False)
    mail_recipient_mode: str = os.getenv("MAIL_RECIPIENT_MODE", "TEST").upper()
    mail_test_recipient: str = os.getenv("MAIL_TEST_RECIPIENT", "")
    mail_allow_original_recipient: bool = _bool("MAIL_ALLOW_ORIGINAL_RECIPIENT", False)

    @property
    def report_search_verify(self) -> bool | str:
        if self.report_search_ca_bundle:
            return self.report_search_ca_bundle
        return self.report_search_verify_ssl

    def validate(self) -> None:
        required = {
            "MYSQL_PASSWORD": self.mysql_password,
            "POP3_HOST": self.pop3_host,
            "POP3_USER": self.pop3_user,
            "POP3_PASSWORD": self.pop3_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required settings: {', '.join(missing)}")
        if self.max_analysis_per_run < 1:
            raise RuntimeError("MAX_ANALYSIS_PER_RUN must be at least 1")
        if self.analysis_interval_seconds < 0:
            raise RuntimeError("ANALYSIS_INTERVAL_SECONDS must be 0 or greater")


settings = Settings()
