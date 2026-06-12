---
description: 오늘의 시황 또는 맞춤 주제의 카드뉴스 5장 생성 (인스타 스타일, 텔레그램/인스타 발행 옵션)
argument-hint: [--no-send] [--instagram] [--topic <topic_text>] [--lang <lang_code>]
allowed-tools: Bash, Read
---

# Daily Card News

noslip_quant_bot 데이터 혹은 사용자가 지정한 맞춤 주제(--topic)로 1080x1080 카드뉴스 5장을 생성한다. 일본어(--lang ja) 등 다국어 번역 및 출력을 지원한다.

## 실행 절차

1. 저장소 루트에서 실행한다. 인자가 없으면 `--no-send`를 기본으로 사용한다 (로컬 미리보기):

```bash
services/trader/.venv/bin/python services/trader/daily_card_news.py $ARGUMENTS
```

- 인자 없이 사용자가 "전송"을 원하면 플래그 없이 실행 (텔레그램 앨범 전송).
- `--instagram` 포함 시 인스타그램 캐러셀로도 발행됨을 사용자에게 알린다.
- `--topic "주제"` 지정 시 해당 주제에 맞춰 AI가 슬라이드 내용을 생성하고 전송한다.
- `--lang ja` (또는 jp) 지정 시 일본어로 카드뉴스를 번역 및 생성한다.

2. 출력 JSON의 `cards` 경로 5개를 확인하고, 가능하면 카드 PNG를 Read로 보여준다.

3. 산출물 위치(`data/card_news/YYYYMMDD/` 혹은 `data/card_news/YYYYMMDD_topic/`)와 자동화 상태(run_daily.sh)를 한 줄로 안내한다.
