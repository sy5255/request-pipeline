# Knox 자동 메일링 운영 가이드

## 즉시 중단

분석 파이프라인은 유지하고 자동 메일 발송만 중단하려면 다음 값을 사용합니다.

```env
MAIL_SEND_ENABLED=false
```

이 상태에서는 Knox API를 호출하지 않습니다. 분석 완료 결과는 `send_status=SEND_BLOCKED`로 유지됩니다.

## 수신자 허용목록

자동 발송 가능한 이메일 주소는 `MAIL_ALLOWED_RECIPIENTS`에 미리 등록합니다.

```env
MAIL_ALLOWED_RECIPIENTS=user01@example.com,user02@example.com,user03@example.com
```

허용목록은 다음 원칙으로 동작합니다.

- 이메일 주소 전체를 정확하게 비교합니다.
- 대소문자는 구분하지 않습니다.
- 도메인 전체 허용이나 와일드카드는 지원하지 않습니다.
- `TEST`와 `ORIGINAL` 모드 모두 허용목록 검사를 수행합니다.
- 허용목록이 비어 있거나 대상 주소가 목록에 없으면 Knox API 호출 전에 발송을 차단합니다.
- 목록에 없는 주소가 감지되면 해당 요청은 발송 실패 상태로 기록되며, 다른 주소로 임의 대체하지 않습니다.

부서 구성원이 변경되면 운영 `.env`의 목록을 수정한 후 실행 프로세스를 재시작해야 합니다.

## 테스트 수신처로 전체 우회

모든 분석 결과 메일을 동일한 테스트 계정으로 보내려면 다음처럼 설정합니다.

```env
MAIL_SEND_ENABLED=true
MAIL_RECIPIENT_MODE=TEST
MAIL_TEST_RECIPIENT=your-account@example.com
MAIL_ALLOWED_RECIPIENTS=your-account@example.com
MAIL_ALLOW_ORIGINAL_RECIPIENT=false
MAIL_SEND_BATCH_SIZE=1
```

`TEST` 모드에서는 DB의 `sender_email`과 `reply_to_email`을 사용하지 않습니다. 모든 메일은 `MAIL_TEST_RECIPIENT` 한 곳으로만 발송되며, 이 주소도 허용목록에 등록되어 있어야 합니다.

초기 검증에서는 `MAIL_SEND_BATCH_SIZE=1`을 권장합니다. 현재 DB에 쌓인 `SEND_BLOCKED` 행이 오래된 데이터까지 포함할 수 있기 때문입니다.

## 실제 요청자 발송

실제 요청자에게 보내려면 다음 설정을 함께 명시해야 합니다.

```env
MAIL_SEND_ENABLED=true
MAIL_RECIPIENT_MODE=ORIGINAL
MAIL_ALLOW_ORIGINAL_RECIPIENT=true
MAIL_ALLOWED_RECIPIENTS=user01@example.com,user02@example.com
```

`ORIGINAL` 모드는 `reply_to_email`, `sender_email`, `original_recipient_email` 순서로 수신자를 선택합니다. 선택된 주소가 허용목록에 없으면 메일을 발송하지 않습니다.

## HTML 링크 발송

연관 링크를 클릭 가능한 링크로 보내려면 다음 값을 사용합니다.

```env
KNOX_MAIL_CONTENT_TYPE=HTML
```

HTML 본문은 Knox API에 `multipart/form-data`의 `mail` 필드로 전달합니다. `mail` 필드 안에는 `contentType=HTML`과 HTML 본문이 포함된 JSON 문자열이 들어갑니다.

## 상태 흐름

```text
SEND_BLOCKED 또는 SEND_PENDING
→ SENDING
→ SENT
```

명확한 HTTP 실패나 허용목록 차단은 `SEND_BLOCKED`로 복귀합니다. 타임아웃이나 연결 종료처럼 실제 발송 여부를 판단할 수 없는 경우에는 중복 발송을 막기 위해 `SEND_UNKNOWN`으로 전환합니다.

성공 시 다음 값이 저장됩니다.

```text
send_status = SENT
sent_mail_id = Knox 응답의 메일 ID
sent_at = 현재 시각
actual_recipient_email = 실제 발송 주소
recipient_mode = TEST 또는 ORIGINAL
```

## Stage 설정 예시

```env
MAIL_SEND_ENABLED=true
MAIL_RECIPIENT_MODE=TEST
MAIL_TEST_RECIPIENT=your-account@example.com
MAIL_ALLOWED_RECIPIENTS=your-account@example.com,team-member@example.com
MAIL_ALLOW_ORIGINAL_RECIPIENT=false
MAIL_SEND_BATCH_SIZE=1
MAIL_SEND_STALE_MINUTES=15
MAIL_SUBJECT_PREFIX=[IFA Curator 분석 결과]

KNOX_MAIL_API_URL=https://openapi.stage.example.com/mail/api/v2.0/mails/send
KNOX_MAIL_USER_ID=agent
KNOX_MAIL_AUTH_TOKEN=change-me
KNOX_MAIL_SYSTEM_ID=change-me
KNOX_MAIL_SENDER_EMAIL=agent@example.com
KNOX_MAIL_DOC_SECU_TYPE=PERSONAL
KNOX_MAIL_CONTENT_TYPE=HTML
KNOX_MAIL_CONNECT_TIMEOUT=10
KNOX_MAIL_READ_TIMEOUT=30
KNOX_MAIL_VERIFY_SSL=true
KNOX_MAIL_CA_BUNDLE=
```
