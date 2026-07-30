# request-pipeline

IFA Curator 요청 메일을 POP3로 수집하고 `report-search` 내부 분석 API와 연동하는 단일 스케줄러 프로젝트입니다.

## 현재 구현 범위

- POP3 UIDL 기반 신규 메일 수집
- 제목·본문·발신자·Reply-To 파싱
- 대상 제목 접두어 판별
- Unicode NFKC 및 SHA-256 제목 정규화
- 동일 제목 72시간 중복 판정
- MySQL `request_mail` 저장
- `/internal/email-analysis` 호출
- 실패 건 재시도
- 실행당 분석 건수 제한 및 호출 간격 적용
- 첫 분석 실패 시 해당 실행 즉시 중단
- 메일 발송 완전 차단 상태

Raw EML과 첨부파일은 파일시스템에 저장하지 않습니다.

## 실행

```bash
cp .env.example .env
pip install -r requirements.txt
python -m request_pipeline.run
```

스케줄러에는 위 명령 하나만 등록합니다.

## 분석 API 호출 보호 설정

사내 보안 게이트웨이의 연속 요청 차단을 피하기 위해 RETRY와 신규 메일을 합쳐 한 번의 실행에서 처리할 최대 분석 건수와 호출 간격을 설정합니다.

```env
MAX_ANALYSIS_PER_RUN=3
ANALYSIS_INTERVAL_SECONDS=3
```

동작 방식은 다음과 같습니다.

1. `RETRY` 상태 요청을 먼저 최대 설정 건수까지 처리합니다.
2. RETRY 처리 후 남은 한도만큼 신규 메일을 분석합니다.
3. 분석 API 호출 사이에 설정한 시간만큼 대기합니다.
4. 한 건이라도 실패하면 추가 API 호출을 중단합니다.
5. 다음 스케줄 실행에서 남은 요청을 이어서 처리합니다.

## HTTPS 인증서 설정

운영 환경에서는 SSL 검증을 유지하고 사내 CA 인증서 PEM 파일을 지정하는 방식을 권장합니다.

```env
REPORT_SEARCH_VERIFY_SSL=true
REPORT_SEARCH_CA_BUNDLE=/config/certs/samsungds-root-ca.pem
```

개발 환경에서 사내 인증서 체인을 신뢰할 수 없어 임시 확인이 필요할 때만 다음처럼 사용합니다.

```env
REPORT_SEARCH_VERIFY_SSL=false
REPORT_SEARCH_CA_BUNDLE=
```

`REPORT_SEARCH_VERIFY_SSL=false`는 서버 인증서 검증을 생략하므로 운영 환경에서는 사용하지 않는 것이 원칙입니다.

## 발송 안전장치

현재 `mail_sender.py`는 아직 구현하지 않았습니다. `.env`에서 다음 값을 유지하십시오.

```env
MAIL_SEND_ENABLED=false
MAIL_RECIPIENT_MODE=TEST
MAIL_ALLOW_ORIGINAL_RECIPIENT=false
```

분석 완료 건은 `SEND_BLOCKED` 상태로 저장되며 메일 발송 API는 호출되지 않습니다.

## 임시 내부 서비스 키

개발 단계에서는 두 저장소가 다음 값을 공유합니다.

```env
REPORT_SEARCH_SERVICE_KEY=dev-only-change-this-key
```

운영 전에는 긴 무작위 값으로 교체해야 합니다.
