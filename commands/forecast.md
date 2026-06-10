---
description: 모든 시계열 zero-shot/개인화 예측 — 금융·반도체 공정·양자에러(Stim)·범용
argument-hint: <csv경로> [domain] [days] | train <user> <name> <csv> [domain] | serve <user> <name> [days]
allowed-tools: Bash, Read
---

# Universal Forecast (zero-shot / personalized)

주식·코인뿐 아니라 **반도체 공정 지표**(수율 %, 결함밀도 — SPC 관리한계 이탈 감지), **양자에러 데이터**(Stim logical error rate, 라운드/샷 인덱스, 로그스케일), 범용 시계열까지 예측한다.

## 인자 해석 (`$ARGUMENTS`)

- `<csv경로> [domain] [days]` → zero-shot 즉시 예측 (domain: finance|semiconductor|quantum|generic, 기본 generic)
- `train <user> <name> <csv> [domain]` → 데이터셋 등록 + 개인화 모델 학습(하이퍼파라미터 탐색, holdout MAPE 리포트)
- `serve <user> <name> [days]` → 저장된 개인화 모델로 예측 서빙 + 이상치 리포트
- `demo` → 반도체/Stim 데모 CSV 생성

## 실행 (저장소 루트, `.venv` 없으면 python3)

```bash
# zero-shot
services/trader/.venv/bin/python services/trader/personal_forecast_service.py zeroshot --csv <CSV> --domain <DOMAIN> --days <N>

# 개인화 학습 → 서빙
services/trader/.venv/bin/python services/trader/personal_forecast_service.py register --user <USER> --name <NAME> --csv <CSV> --domain <DOMAIN>
services/trader/.venv/bin/python services/trader/personal_forecast_service.py train --user <USER> --name <NAME>
services/trader/.venv/bin/python services/trader/personal_forecast_service.py forecast --user <USER> --name <NAME> --days <N>

# 데모 데이터
services/trader/.venv/bin/python services/trader/personal_forecast_service.py stim-demo
```

## 응답 방법

JSON 결과의 예측치·80% 밴드·변화율·`anomalies_recent`(관리한계 이탈)를 요약하고, `chart` PNG를 Read로 보여준다. quantum 도메인은 에러율이 로그스케일임을, semiconductor는 이상치가 공정 excursion 후보임을 짚어 준다. CSV의 시간 컬럼은 date/round/shot/step 모두 인식됨을 안내한다.
