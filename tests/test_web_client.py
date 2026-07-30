from types import SimpleNamespace
from unittest.mock import patch

import requests

from request_pipeline.web_client import _is_gateway_block, analyze_request


def _response(status_code: int, body: str = "", reason: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    response.reason = reason
    response.headers["Content-Type"] = "application/json"
    return response


def _settings():
    return SimpleNamespace(
        report_search_base_url="https://example.internal",
        report_search_service_key="service-key",
        report_search_verify=False,
        web_api_connect_timeout=10,
        web_api_read_timeout=180,
    )


def _profile(instruction_template: str | None):
    return {
        "profile_key": "defect-analysis",
        "base_url": "https://example.internal",
        "endpoint_path": "/internal/email-analysis",
        "http_method": "POST",
        "auth_header_name": "X-Internal-Service-Key",
        "auth_env_name": "MISSING_TEST_API_KEY",
        "headers_json": {
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        "request_template_json": {
            "request_id": "{{request_id}}",
            "request_title": "{{request_title}}",
            "instruction_prompt": "{{instruction_prompt}}",
            "mail_body": "{{mail_body}}",
        },
        "response_config_json": {
            "status_field": "status",
            "success_values": ["COMPLETED"],
            "answer_field": "answer_text",
            "search_results_field": "search_results",
            "trace_field": "trace",
            "trace_mapping": {},
        },
        "instruction_template": instruction_template,
        "verify_ssl": False,
    }


def test_detects_mwg_gateway_block_page():
    response = _response(403, '<img src="/mwg-internal/block.jpg">')
    assert _is_gateway_block(response)


def test_detects_new_all_deny_reason():
    response = _response(403, reason="New_All_deny_Page")
    assert _is_gateway_block(response)


def test_regular_403_is_not_classified_as_gateway_block():
    response = _response(403, '{"detail":"Forbidden"}', "Forbidden")
    assert not _is_gateway_block(response)


def test_non_403_is_not_classified_as_gateway_block():
    response = _response(200, "ok")
    assert not _is_gateway_block(response)


def test_instruction_and_request_title_are_sent_separately():
    response = _response(
        200,
        '{"status":"COMPLETED","answer_text":"ok","search_results":[],"trace":{}}',
    )
    instruction = (
        "아래 텍스트는 새로 들어온 불량분석 의뢰제목이야. "
        "이전에 이와 비슷한 분석 이력이 있는지 검색해줘."
        "\n\n불량분석 의뢰제목:\n{{raw_request_title}}"
    )

    with patch("request_pipeline.web_client.requests.request", return_value=response) as request:
        analyze_request(
            _settings(),
            profile=_profile(instruction),
            request_id=10,
            requester_user_id="user01",
            requester_email="user01@example.com",
            request_title="ABC Lot Contact 불량",
            mail_body="메일 본문",
            route_case="DEFECT_ANALYSIS_REQUEST",
            route_rule_key="api_defect_analysis_v1",
        )

    payload = request.call_args.kwargs["json"]
    assert payload["request_title"] == "ABC Lot Contact 불량"
    assert payload["instruction_prompt"].startswith(
        "아래 텍스트는 새로 들어온 불량분석 의뢰제목이야."
    )
    assert payload["instruction_prompt"].endswith("ABC Lot Contact 불량")
    assert payload["mail_body"] == "메일 본문"


def test_profile_without_instruction_sends_empty_instruction():
    response = _response(
        200,
        '{"status":"COMPLETED","answer_text":"ok","search_results":[],"trace":{}}',
    )

    with patch("request_pipeline.web_client.requests.request", return_value=response) as request:
        analyze_request(
            _settings(),
            profile=_profile(None),
            request_id=11,
            requester_user_id="user01",
            requester_email="user01@example.com",
            request_title="원본 제목",
            mail_body="",
        )

    payload = request.call_args.kwargs["json"]
    assert payload["request_title"] == "원본 제목"
    assert payload["instruction_prompt"] == ""
