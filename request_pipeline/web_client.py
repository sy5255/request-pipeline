from typing import Any

import requests

from request_pipeline.config import Settings


def analyze_request(
    settings: Settings,
    *,
    request_id: int,
    requester_user_id: str,
    requester_email: str,
    request_title: str,
    mail_body: str,
) -> dict[str, Any]:
    response = requests.post(
        f"{settings.report_search_base_url}/internal/email-analysis",
        headers={
            "X-Internal-Service-Key": settings.report_search_service_key,
            "Content-Type": "application/json",
        },
        json={
            "request_id": request_id,
            "requester_user_id": requester_user_id,
            "requester_email": requester_email,
            "request_title": request_title,
            "mail_body": mail_body,
        },
        timeout=(settings.web_api_connect_timeout, settings.web_api_read_timeout),
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "COMPLETED":
        raise RuntimeError(f"Unexpected analysis status: {data.get('status')}")
    return data
