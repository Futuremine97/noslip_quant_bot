---
description: 한국 증권사 OPEN API 키를 터미널에서 안전하게 .env에 등록하는 대화형 마법사
argument-hint: [브로커ID]  예: toss | kis | kiwoom | kb | shinhan | nh | hana | yuanta
allowed-tools: Bash
---

# Broker API Key Setup Wizard

증권사 OPEN API 키를 터미널 대화형 마법사로 등록한다.
시크릿 값은 화면에 표시되지 않으며(raw TTY 숨김 입력) .env 파일에만 저장된다.

## 지원 증권사

| ID        | 증권사               |
|-----------|----------------------|
| toss      | 토스증권             |
| kis       | 한국투자증권 (KIS)   |
| kiwoom    | 키움증권             |
| kb        | KB증권               |
| shinhan   | 신한투자증권         |
| nh        | NH투자증권           |
| hana      | 하나증권             |
| yuanta    | 유안타증권 (Windows) |

## 실행

```bash
# 전체 증권사 선택 메뉴
node bin/setup_broker.js

# 특정 증권사만 (인자 전달 시)
node bin/setup_broker.js $ARGUMENTS
```

또는 CLI로:

```bash
noslip setup
noslip setup $ARGUMENTS
```

## 동작 방식

1. .gitignore에 .env가 없으면 경고 후 계속 여부 확인
2. 등록할 증권사 선택 (숫자 입력, 0=전체)
3. 각 증권사의 필드를 순서대로 입력
   - 시크릿(App Secret, Client Secret 등)은 **입력 시 화면에 * 만 표시**
   - 이미 설정된 시크릿은 업데이트 여부를 별도로 확인
4. 입력 내용 미리보기 (시크릿은 마스킹) 후 저장 여부 확인
5. .env 파일에 원자적으로 저장 (mode 0600)

## 보안 정책

- 시크릿은 절대 로그·커밋·Telegram·MCP 프롬프트에 포함하지 않는다 (SECURITY_BROKER.md)
- 모드는 `read_only`에서 시작해 충분히 검증 후 `live`로 변경
- 등록 완료 후 `noslip broker <id>` 로 연결 상태를 확인한다
