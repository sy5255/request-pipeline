import copy
import os
from typing import Any
from urllib.parse import urljoin

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


def _lookup_path(data: Any, path: str | None, default: Any = None) -> Any:
    if not path:
        return default
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value

    if value.startswith("{{") and value.endswith("}}"):
        key = value[2:-2].strip()
        return context.get(key)

    rendered = value
    for key, item in context.items():
        rendered = rendered.replace(
            f"{{{{{key}}}}}",
            "" if item is None else str(item),
        )
    return rendered


def _build_instruction_prompt(
    profile: dict[str, Any],
    context: dict[str, Any],
) -> str:
    template = str(profile.get("instruction_template") or "").strip()
    if not template:
        return ""

    rendered = _render_template(template, context)
    if not isinstance(rendered, str):
        raise RuntimeError(
            "API profile instruction_template must render to text: "
            f"{profile.get('profile_key')}"
        )
    return rendered.strip()


def _resolve_verify(settings: Settings, profile: dict[str, Any]) -> bool | str:
    ca_env_name = profile.get("ca_bundle_env_name")
    if ca_env_name:
        ca_path = os.getenv(str(ca_env_name), "").strip()
        if ca_path:
            return ca_path

    profile_verify = profile.get("verify_ssl")
    if profile_verify is not None:
        return bool(profile_verify)

    return settings.report_search_verify


def _resolve_base_url(settings: Settings, profile: dict[str, Any]) -> str:
    env_name = profile.get("base_url_env_name")
    if env_name:
        env_value = os.getenv(str(env_name), "").strip()
        if env_value:
            return env_value.rstrip("/")

    base_url = str(profile.get("base_url") or "").strip()
    if base_url:
        return base_url.rstrip("/")

    if profile.get("profile_key") == "defect-analysis":
        return settings.report_search_base_url

    raise RuntimeError(
        f"API profile has no base URL: {profile.get('profile_key')}"
    )


def _normalize_response(
    data: dict[str, Any],
    response_config: dict[str, Any],
) -> dict[str, Any]:
    status_field = response_config.get("status_field")
    success_values = response_config.get("success_values") or []
    if status_field:
        status = _lookup_path(data, str(status_field))
        if success_values and status not in success_values:
            raise RuntimeError(f"Unexpected API status: {status}")

    trace_source = _lookup_path(
        data,
        response_config.get("trace_field"),
        {},
    ) or {}
    trace_mapping = response_config.get("trace_mapping") or {}
    trace = {
        normalized_key: _lookup_path(trace_source, source_path)
        for normalized_key, source_path in trace_mapping.items()
    }

    return {
        "status": "COMPLETED",
        "answer_text": _lookup_path(
            data,
            response_config.get("answer_field"),
            "",
        ),
        "search_results": _lookup_path(
            data,
            response_config.get("search_results_field"),
            [],
        ) or [],
        "trace": trace,
        "raw_response": data,
    }


def analyze_request(
    settings: Settings,
    *,
    profile: dict[str, Any],
    request_id: int,
    requester_user_id: str,
    requester_email: str,
    request_title: str,
    mail_body: str,
    route_case: str | None = None,
    route_rule_key: str | None = None,
) -> dict[str, Any]:
    # 검색에는 원본 의뢰 제목만 사용하고, 답변 지시문은 별도 필드로 전달합니다.
    base_context = {
        "request_id": request_id,
        "requester_user_id": requester_user_id,
        "requester_email": requester_email,
        "raw_request_title": request_title,
        "mail_body": mail_body,
        "route_case": route_case,
        "route_rule_key": route_rule_key,
    }
    instruction_prompt = _build_instruction_prompt(profile, base_context)

    context = {
        **base_context,
        "request_title": request_title,
        "instruction_prompt": instruction_prompt,
    }

    base_url = _resolve_base_url(settings, profile)
    endpoint_path = str(profile.get("endpoint_path") or "").lstrip("/")
    url = urljoin(base_url.rstrip("/") + "/", endpoint_path)

    headers = copy.deepcopy(profile.get("headers_json") or {})
    auth_header_name = profile.get("auth_header_name")
    auth_env_name = profile.get("auth_env_name")
    if auth_header_name and auth_env_name:
        auth_value = os.getenv(str(auth_env_name), "")
        if not auth_value and profile.get("profile_key") == "defect-analysis":
            auth_value = settings.report_search_service_key
        if not auth_value:
            raise RuntimeError(
                f"Missing API credential environment variable: {auth_env_name}"
            )
        headers[str(auth_header_name)] = auth_value

    payload = _render_template(
        copy.deepcopy(profile.get("request_template_json") or {}),
        context,
    )

    verify = _resolve_verify(settings, profile)
    if verify is False:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    connect_timeout = int(
        profile.get("connect_timeout_seconds")
        or settings.web_api_connect_timeout
    )
    read_timeout = int(
        profile.get("read_timeout_seconds")
        or settings.web_api_read_timeout
    )

    method = str(profile.get("http_method") or "POST").upper()
    response = requests.request(
        method,
        url,
        headers=headers,
        json=payload,
        timeout=(connect_timeout, read_timeout),
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
            "API가 JSON이 아닌 응답을 반환했습니다: "
            f"profile={profile.get('profile_key')}, "
            f"status={response.status_code}, "
            f"content_type={response.headers.get('Content-Type')}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"API response must be a JSON object: {profile.get('profile_key')}"
        )

    return _normalize_response(
        data,
        profile.get("response_config_json") or {},
    )
