---
description: 한국 증권사 OPEN API 차익거래 스캔 (2자간/3자간/다자간) — 조회·프리뷰 전용
argument-hint: [two|route|multi|all] [--demo] [--plan] [종목코드,...]
allowed-tools: Bash
---

# Korea Cross-Broker Arbitrage

KIS·키움·KB·신한·NH·하나 등 증권사 OPEN API 시세를 교차 비교해 차익 기회를 찾는다. 수수료+증권거래세+슬리피지 차감 후 net edge(bp) 기준. **주문 제출은 절대 하지 않는다** — 프리뷰(prepare_order)까지만.

## 인자 해석 (`$ARGUMENTS`)

- 모드: `two`(2자간 브로커 간), `route`(설정된 3자간+ 라우트), `multi`(Bellman-Ford 다자간 사이클), 기본 `all`
- `--demo`: 자격증명 없이 합성 시세로 테스트
- `--plan`: 최상위 기회의 레그별 주문 프리뷰 JSON 포함
- 종목코드 목록(쉼표구분) 지정 가능, 미지정 시 watchlist

## 실행 (저장소 루트)

```bash
services/trader/.venv/bin/python services/trader/korea_arbitrage.py report [--demo]
services/trader/.venv/bin/python services/trader/korea_arbitrage.py scan --mode <MODE> [--demo] [--symbols 005930,000660]
services/trader/.venv/bin/python services/trader/korea_arbitrage.py plan [--demo] [--notional 10000000]
```

## 응답 방법

기회별 net edge(bp)와 레그 구성을 보여준다. 다음을 반드시 안내한다: ① 본 시스템은 주문을 제출하지 않으며 실제 체결은 계좌 소유자가 직접 수행 ② 레그 간 체결 시차·부분체결 리스크 ③ 동일 KRX 종목의 브로커 간 괴리는 주로 KRX/NXT 라우팅·시세 지연에서 발생하며 지속 시간이 짧음 ④ 투자 자문 아님. 브로커 미연결 시 `.env` 자격증명 설정(SECURITY_BROKER.md)을 안내한다.
