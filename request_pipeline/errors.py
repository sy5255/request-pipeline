"""분석 API 실패를 일시적/영구적 오류로 분류합니다.

일시적 오류는 개발 서버 재기동, 게이트웨이 차단, 백엔드 5xx처럼 시간이
지나면 저절로 해소되는 실패입니다. 이런 실패로 `FAILED`가 된 요청만
다음 스케줄러 실행에서 다시 대기열로 돌아옵니다.

분류는 실패가 발생한 시점에 예외 객체로 판단하고 결과를
`ae_llm_agent_mail.last_error_kind`에 저장합니다. 다음 실행에서
`last_error` 문자열을 다시 해석하지 않기 위한 구조이며, 이 컬럼이
비어 있는 행은 소생 대상이 아닙니다.
"""

import requests

TRANSIENT = "TRANSIENT"
PERMANENT = "PERMANENT"

# 재요청하면 해소될 수 있는 HTTP 상태 코드만 등록합니다.
# 그 외 4xx는 인증 키, 경로, 페이로드 문제로 보고 영구 오류로 처리합니다.
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


class GatewayBlockedError(RuntimeError):
    """사내 웹 게이트웨이가 분석 요청을 차단했을 때 발생합니다."""


def classify_error(exc: BaseException) -> str:
    """예외를 TRANSIENT 또는 PERMANENT로 분류합니다."""
    # 게이트웨이 차단은 요청 간격을 벌리면 해소되므로 일시적 오류입니다.
    if isinstance(exc, GatewayBlockedError):
        return TRANSIENT

    # 인증서 설정 오류는 시간이 지나도 해소되지 않습니다.
    # requests.exceptions.SSLError가 ConnectionError의 하위 클래스이므로
    # 연결 오류보다 먼저 판정해야 합니다.
    if isinstance(exc, requests.exceptions.SSLError):
        return PERMANENT

    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in TRANSIENT_STATUS_CODES:
            return TRANSIENT
        return PERMANENT

    if isinstance(
        exc,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return TRANSIENT

    return PERMANENT
