---
description: 온체인 고래 입금 감지 리포트 — BTC/ETH $1M+ 거래소 입금과 흔들기 위험 예측
argument-hint: [scan | report | evaluate]
allowed-tools: Bash
---

# On-chain Whale Monitor

Blockchair API로 BTC/ETH 대형 트랜잭션($1M+, whale_config.json에서 조정)을 스캔하고, 거래소 입금 여부를 태깅해 "흔들기 점수"(0~100)와 과거 실현 결과로 보정된 4시간 내 -1% 확률을 보여준다.

## 실행

저장소 루트에서 (`$ARGUMENTS` 비어 있으면 `report`):

```bash
services/trader/.venv/bin/python services/trader/whale_onchain_monitor.py report
```

- `scan` → 신규 이벤트 수집만
- `evaluate` → 과거 이벤트 결과 라벨링(예측 보정)
- 상시 감시는 `com.noslip.onchain.plist` 데몬(5분 주기, 경보 점수 이상 시 텔레그램 알림) 안내

## 응답 방법

리포트 전문을 보여주고, 🚨(경보) 이벤트가 있으면 강조한다. 거래소 입금 = 매도압력 위험, 학습표본 수가 적으면 확률 신뢰도가 낮음을 한 줄로 안내한다. 투자 자문이 아님을 명시한다.
