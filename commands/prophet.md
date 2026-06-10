---
description: Prophet 시계열 예측 + 시각화 차트 생성 (주식/암호화폐)
argument-hint: <symbol> [days]
allowed-tools: Bash, Read
---

# Prophet Forecast

사용자가 요청한 종목에 대해 Prophet 시계열 예측을 실행하고 시각화한다.

## 인자

`$ARGUMENTS` = `<symbol> [days]`

- `symbol` (필수): TSLA, NVDA, BTC, ETH, SOL, BTC-USD 등
- `days` (선택): 예측 기간(일). 기본 30, 범위 5~365

## 실행 절차

1. 저장소 루트에서 아래 명령을 실행한다 (인자가 비어 있으면 사용자에게 종목을 물어본다):

```bash
services/trader/.venv/bin/python services/trader/prophet_forecast.py $ARGUMENTS --json
```

- `.venv`가 없으면 `python3 services/trader/prophet_forecast.py $ARGUMENTS --json`으로 대체한다.
- `prophet` 또는 `yfinance` 미설치 오류 시: `services/trader/.venv/bin/pip install prophet yfinance matplotlib` 후 재시도한다.

2. JSON 출력의 `report`(예측 요약)와 `photo`(차트 PNG 경로)를 확인한다.

3. 사용자에게 다음을 보여준다:
   - 예측 리포트 전문 (현재가, 예측가, 신뢰구간, 추세 기울기, 주간 계절성, 시그널)
   - 차트 파일 경로 안내 (가능한 클라이언트라면 PNG를 Read 해서 직접 보여준다)

4. 마지막에 한 줄 해석을 덧붙인다: 추세 방향과 신뢰구간 폭(불확실성)에 대한 간단한 코멘트. 투자 자문이 아님을 명시한다.
