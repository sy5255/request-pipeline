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

`email-ingestion`의 `ingest_pop3.py`는 기본값 `FILE_ARCHIVE_MODE=NIGHT`로 동작합니다.
재전송 등으로 발송이 취소된 메일이 파일로 남지 않도록, FILE_ARCHIVE 메일의 파일 저장을 미룹니다.

| 실행 | 동작 |
|---|---|
| `python ingest_pop3.py` (04:00~05:00 외) | 신규 메일 수집·분류·DB 기록만. FILE_ARCHIVE 메일은 `ROUTED`(저장 대기) |
| `python ingest_pop3.py` (서버 시각 04:00~05:00) | 수집·기록 후 저장 대기 메일(ROUTED/RETRY)을 파일로 저장 |
| `python ingest_pop3.py --archive-only` (시간 무관) | 수집 없이 저장 대기 메일만 파일로 저장 |

- 저장 시점에 POP3에서 uidl로 원본을 다시 받습니다. 원본이 사라진 메일(발송 취소)은 저장하지 않고 `SOURCE_MISSING`으로 종료합니다.
- `RETRY`(일시 오류 후 재시도 대기)도 저장 대상이며, `FAILED`(재시도 횟수 초과)는 제외됩니다.
- 자동 저장 시간대: `FILE_ARCHIVE_AUTO_START_HOUR=4`, `FILE_ARCHIVE_AUTO_END_HOUR=5` (끝 시각 미포함)
- 예전처럼 수집 즉시 저장하려면 `FILE_ARCHIVE_MODE=IMMEDIATE`

1시간 주기 스케줄러로 `python ingest_pop3.py`만 등록해도 04시대 실행에서 자동으로 저장됩니다.

## 메일 발송 상태

- `SEND_PENDING`: 현재 발송 대기
- `SEND_BLOCKED`: 발송 차단 또는 명확한 실패. `MAIL_SEND_ENABLED=true`인 다음 실행에서 재시도
- `SEND_DROPPED`: DBeaver 수동 검수로 영구 제외
- `SEND_UNKNOWN`: 실제 발송 여부가 불확실해 자동 재시도 제외
- `SENDING`: Knox API 호출 중
- `SENT`: 발송 완료

자동 발송 큐는 `SEND_BLOCKED`, `SEND_PENDING`만 조회합니다.
