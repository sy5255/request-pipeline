import requests

from request_pipeline.web_client import _is_gateway_block


def _response(status_code: int, body: str = "", reason: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    response.reason = reason
    return response


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
