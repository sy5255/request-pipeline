# email-ingestion 연계 운영

`email-ingestion`과 `request-pipeline`은 역할을 분리합니다.

- `email-ingestion`: POP3 수집, DB 라우팅, `FILE_ARCHIVE` sharedworkspace 저장
- `request-pipeline`: `API_ANALYSIS` 실행, 분석 결과 저장, Knox 메일 발송

## 주기 실행

```bash
cd /config/work/email-ingestion && python ingest_pop3.py
cd /config/work/request-pipeline && python -m request_pipeline.run
```

두 프로그램 모두 한 번 실행하고 종료합니다. 각 실행에서 스키마를 확인하므로 DDL 보장 로직은 유지합니다.

## 야간 FILE_ARCHIVE

`email-ingestion`에서 다음 설정을 사용합니다.

```env
FILE_ARCHIVE_MODE=NIGHT
```

야간 잡:

```bash
cd /config/work/email-ingestion && python ingest_pop3.py --archive-only
```

발송 취소로 POP3에서 사라진 원본은 `SOURCE_MISSING`으로 종료되고 sharedworkspace에 저장되지 않습니다.

`NIGHT` 모드에서는 일반 실행(`python ingest_pop3.py`)이라도 서버 시각 04:00~05:00 처리 회차에서
FILE_ARCHIVE 저장 대기열(`ROUTED`/`RETRY`)까지 처리합니다. 1시간 주기 스케줄러에 일반 실행만 등록해도
매일 04시대에 파일이 저장됩니다. 시간대는 email-ingestion의 `FILE_ARCHIVE_AUTO_START_HOUR`/`FILE_ARCHIVE_AUTO_END_HOUR`로 바꿀 수 있습니다.

## 메일 발송 상태

- `SEND_PENDING`: 현재 발송 대기
- `SEND_BLOCKED`: 발송 차단 또는 명확한 실패. `MAIL_SEND_ENABLED=true`인 다음 실행에서 재시도
- `SEND_DROPPED`: DBeaver 수동 검수로 영구 제외
- `SEND_UNKNOWN`: 실제 발송 여부가 불확실해 자동 재시도 제외
- `SENDING`: Knox API 호출 중
- `SENT`: 발송 완료

자동 발송 큐는 `SEND_BLOCKED`, `SEND_PENDING`만 조회합니다.
