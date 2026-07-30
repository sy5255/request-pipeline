from typing import Any

import requests
import urllib3

from request_pipeline.config import Settings


class GatewayBlockedError(RuntimeError):
    """사내 웹 게이트웨이가 분석 요청을 차단했을 때 발생합니다."""


def _is_gateway_block(response: requests.Response) -> bool:
    if response.status_code != 403:
        return False

    reason = response.reason or ""
    body = response.text or ""
    return (
        "New_All_deny_Page" in reason
        or "mwg-internal" in body
        or "User-define" in body
    )


def analyze_request(
    settings: Settings,
    *,
    request_id: int,
    requester_user_id: str,
    requester_email: str,
    request_title: str,
    mail_body: str,
) -> dict[str, Any]:
    verify = settings.report_search_verify

    if verify is False:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    response = requests.post(
        f"{settings.report_search_base_url}/internal/email-analysis",
        headers={
            "Accept": "application/json",
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
        verify=verify,
    )

    if _is_gateway_block(response):
        raise GatewayBlockedError(
            "사내 웹 게이트웨이가 분석 요청을 차단했습니다. "
            "요청 간격과 실행당 처리량을 확인하십시오."
        )

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "report-search API가 JSON이 아닌 응답을 반환했습니다: "
            f"status={response.status_code}, content_type={response.headers.get('Content-Type')}"
        ) from exc

    if data.get("status") != "COMPLETED":
        raise RuntimeError(f"Unexpected analysis status: {data.get('status')}")

    return data
