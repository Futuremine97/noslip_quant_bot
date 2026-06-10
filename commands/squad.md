---
description: No Slip Quant 피어 허브 — 플러그인 사용자 목록·알파 시그널 공유/조회
argument-hint: [peers | feed [symbol] | share <symbol> <BUY|SELL|HOLD> <확신도%> [근거] | register <닉네임>]
allowed-tools: Bash
---

# Quant Squad (Peer Hub)

이 플러그인을 쓰는 사용자들끼리 연결되는 커뮤니티 기능. 중앙 서버(prediction_api)를 통해 프레젠스와 알파 시그널을 공유한다.

## 인자 해석 (`$ARGUMENTS`)

- 비어 있음 또는 `peers` → 피어 목록 조회
- `feed [symbol]` → 시그널 피드 조회 (심볼 필터 선택)
- `share <symbol> <BUY|SELL|HOLD> <확신도> [근거...]` → 시그널 공유
- `register <닉네임> [소개...]` → 허브 등록/닉네임 변경

## 실행

저장소 루트에서 (`.venv` 없으면 `python3` 사용):

```bash
# 피어 목록
services/trader/.venv/bin/python services/trader/peer_hub_client.py peers

# 시그널 피드
services/trader/.venv/bin/python services/trader/peer_hub_client.py feed --symbol NVDA

# 시그널 공유
services/trader/.venv/bin/python services/trader/peer_hub_client.py share NVDA BUY 80 --thesis "근거 텍스트"

# 등록
services/trader/.venv/bin/python services/trader/peer_hub_client.py register --nickname 닉네임 --bio "소개"
```

## 안내

- 처음 사용하는 사용자라면 register부터 권한다.
- 서버 연결 실패 시: 중앙 서버(`PREDICTION_API_URL`, 기본 http://localhost:8000)가 실행 중인지, `.env`의 `PREDICTION_API_TOKEN`이 설정됐는지 확인하라고 안내한다.
- 출력 전문을 사용자에게 그대로 보여주고, 컨센서스가 있으면 한 줄 해석을 덧붙인다.
