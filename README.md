# request-pipeline

IFA Curator 요청 메일을 POP3로 수집하고 `report-search` 내부 분석 API와 연동하는 단일 스케줄러 프로젝트입니다.

## 현재 구현 범위

- POP3 UIDL 기반 신규 메일 수집
- 제목·본문·발신자·Reply-To 파싱
- 대상 제목 접두어 판별
- Unicode NFKC 및 SHA-256 제목 정규화
- 동일 제목 72시간 중복 판정
- MySQL `ae_llm_agent_mail` 저장
- 기존 `request_mail` 테이블 자동 이름 변경
- `/internal/email-analysis` 호출
- 실패 건 재시도
- 실행당 분석 건수 제한 및 호출 간격 적용
- 첫 분석 실패 시 해당 실행 즉시 중단
- 비정상 종료 후 미완료 요청 자동 복구
- MySQL advisory lock 기반 중복 실행 방지
- 메일 발송 완전 차단 상태

Raw EML과 첨부파일은 파일시스템에 저장하지 않습니다.

## 실행

```bash
cp .env.example .env
pip install -r requirements.txt
python -m request_pipeline.run
```

스케줄러에는 위 명령 하나만 등록합니다. 프로세스를 하루 종일 실행할 필요는 없으며, 주기 실행할 때 UIDL과 DB 상태를 기준으로 이전 처리 지점부터 이어서 처리합니다.

## 테이블명과 기존 데이터 이전

현재 메일 처리 이력 테이블은 다음 이름을 사용합니다.

```text
ae_llm_agent_mail
```

기존 환경에 `request_mail`만 존재하면 스케줄러 시작 시 advisory lock 안에서 다음 작업을 자동 수행합니다.

```sql
RENAME TABLE request_mail TO ae_llm_agent_mail;
```

따라서 기존 UIDL, 상태, 분석 결과 및 재시도 이력은 그대로 유지됩니다.

두 테이블이 동시에 존재하면 어느 데이터를 기준으로 할지 자동 판단하지 않고 실행을 중단합니다. 이 경우 두 테이블의 데이터를 수동으로 확인하고 하나로 정리한 뒤 다시 실행해야 합니다.

## 무누락 처리 보장 방식

이 프로젝트는 메일을 메모리 위치가 아니라 POP3 UIDL과 MySQL 상태로 추적합니다.

1. POP3에서 조회한 UIDL이 DB에 없으면 신규 메일로 저장합니다.
2. DB 저장 직후 프로세스가 종료되어 `RECEIVED`로 남아도 다음 실행에서 `RETRY`로 복구합니다.
3. 분석 도중 종료되어 오래된 `PROCESSING`으로 남아도 다음 실행에서 `RETRY`로 복구합니다.
4. 분석 API가 성공한 뒤 DB 반영 전에 종료되더라도 같은 `request_id`로 재호출합니다. `report-search` 내부 API의 request_id 멱등성에 의해 중복 결과 생성을 방지합니다.
5. 비대상 메일은 `IGNORED`로 확정하여 복구 대상에서 제외합니다.
6. MySQL advisory lock을 획득한 실행만 테이블 이전, 스키마 확인 및 메일 처리를 수행합니다.
7. MySQL 연결이 끊기면 advisory lock은 자동 해제되므로 강제 종료 이후 다음 스케줄이 다시 실행될 수 있습니다.

다만 POP3 서버에서 메일이 다음 수집 전에 삭제되지 않고 보관되어야 합니다. 다른 POP3 클라이언트가 서버 메일을 삭제하는 설정은 사용하지 않아야 합니다.

## 장애 복구 설정

```env
STALE_PROCESSING_MINUTES=15
PIPELINE_LOCK_NAME=request_pipeline_scheduler
PIPELINE_LOCK_WAIT_SECONDS=0
```

- `STALE_PROCESSING_MINUTES`: 이 시간보다 오래된 `PROCESSING` 요청을 중단된 실행으로 판단합니다.
- `PIPELINE_LOCK_NAME`: 같은 DB를 사용하는 모든 스케줄러 인스턴스가 공유할 lock 이름입니다.
- `PIPELINE_LOCK_WAIT_SECONDS=0`: 이미 다른 실행이 동작 중이면 기다리지 않고 현재 실행을 정상 종료합니다.

## 한 시간 주기 실행 예시

Linux cron 예시:

```cron
0 * * * * cd /config/work/request-pipeline && /usr/bin/python -m request_pipeline.run >> /config/work/request-pipeline/pipeline.log 2>&1
```

중복 실행 방지는 애플리케이션 내부의 MySQL advisory lock이 담당하므로 별도의 `flock` 없이도 동작합니다. 운영 환경에서 이중 보호를 원하면 `flock`을 추가해도 됩니다.

## 분석 API 호출 보호 설정

사내 보안 게이트웨이의 연속 요청 차단을 피하기 위해 RETRY와 신규 메일을 합쳐 한 번의 실행에서 처리할 최대 분석 건수와 호출 간격을 설정합니다.

```env
MAX_ANALYSIS_PER_RUN=3
ANALYSIS_INTERVAL_SECONDS=3
```

동작 방식은 다음과 같습니다.

1. 복구된 요청을 포함한 `RETRY` 상태 요청을 먼저 최대 설정 건수까지 처리합니다.
2. RETRY 처리 후 남은 한도만큼 신규 메일을 분석합니다.
3. 분석 API 호출 사이에 설정한 시간만큼 대기합니다.
4. 한 건이라도 실패하면 추가 API 호출을 중단합니다.
5. 다음 스케줄 실행에서 남은 요청을 이어서 처리합니다.

시간당 대상 메일 유입량이 `MAX_ANALYSIS_PER_RUN`보다 많으면 적체될 수 있으므로 운영 유입량에 맞춰 값을 조정해야 합니다.

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
