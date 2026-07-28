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

    report_search_base_url: str = os.getenv(
        "REPORT_SEARCH_BASE_URL",
        "https://ae-llm-agent--fa-report-search-prod.cdep1.ss.net",
    ).rstrip("/")
    report_search_service_key: str = os.getenv(
        "REPORT_SEARCH_SERVICE_KEY", "dev-only-change-this-key"
    )
    web_api_connect_timeout: int = int(os.getenv("WEB_API_CONNECT_TIMEOUT", "10"))
    web_api_read_timeout: int = int(os.getenv("WEB_API_READ_TIMEOUT", "180"))

    mail_send_enabled: bool = _bool("MAIL_SEND_ENABLED", False)
    mail_recipient_mode: str = os.getenv("MAIL_RECIPIENT_MODE", "TEST").upper()
    mail_test_recipient: str = os.getenv("MAIL_TEST_RECIPIENT", "")
    mail_allow_original_recipient: bool = _bool("MAIL_ALLOW_ORIGINAL_RECIPIENT", False)

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


settings = Settings()
