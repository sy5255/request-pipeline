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
- 메일 발송 완전 차단 상태

Raw EML과 첨부파일은 파일시스템에 저장하지 않습니다.

## 실행

```bash
cp .env.example .env
pip install -r requirements.txt
python -m request_pipeline.run
```

스케줄러에는 위 명령 하나만 등록합니다.

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
