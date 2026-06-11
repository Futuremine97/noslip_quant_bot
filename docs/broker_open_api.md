# NoSlip Securities Open API Integration

## Scope

NoSlip supports optional, non-custodial integrations with Toss Securities and
Yuanta Securities. Local analytics continue to work when both integrations are
disabled.

The implementation separates market analysis from order execution:

```text
NoSlip / MCP / Telegram
        |
        +--> broker service (disabled/read_only/paper/live)
                 |
                 +--> Toss official OAuth2 REST API
                 |
                 +--> loopback HTTP --> Windows bridge --> Yuanta official COM
```

MCP and Telegram expose status, quotes, holdings, and order previews only. They
do not submit, modify, or cancel a securities order.

## Toss Securities

Official references:

- Open API guide: <https://developers.tossinvest.com/>
- Server-owned OpenAPI JSON:
  <https://openapi.tossinvest.com/openapi-docs/latest/openapi.json>
- Base API server: `https://openapi.tossinvest.com`

The official API uses OAuth 2.0 Client Credentials. Account, asset, and order
requests also require `X-Tossinvest-Account`. A client has one active access
token at a time, so unnecessary token reissuance invalidates the prior token.
The NoSlip client caches the token in memory until shortly before expiration.

Supported client operations:

- accounts
- current prices, up to 200 symbols in the upstream API
- holdings
- open/closed order history
- order preview
- library-level live order and cancellation methods behind explicit safety
  gates

NoSlip does not expose live order submission through MCP or Telegram. Library
submission requires all three conditions:

1. `TOSS_SECURITIES_MODE=live`
2. `TOSS_SECURITIES_ALLOW_LIVE_ORDERS=true`
3. exact runtime confirmation `SUBMIT_LIVE_ORDER`

Order requests use decimal strings, validate LIMIT/MARKET rules, and include a
client order ID for upstream idempotency.

Read-only setup:

```bash
TOSS_SECURITIES_MODE=read_only
TOSS_SECURITIES_CLIENT_ID=...
TOSS_SECURITIES_CLIENT_SECRET=...
TOSS_SECURITIES_ACCOUNT_SEQ=...
```

Use `read_only` first. The official specification does not publish a separate
sandbox server, so `paper` and order preview modes must not be treated as an
upstream test exchange.

## Yuanta Securities

Official references:

- Service introduction:
  <https://www.myasset.com/myasset/trading/apiSvc/TR_1604001_P1.cmd>
- Official module and development manual:
  <https://www.myasset.com/myasset/trading/apiSvc/TR_1604003_P1.cmd>

Yuanta's official tRadar Open API is not a public REST API. The vendor provides
Windows COM and DLL modules:

- C/C++ through the standard DLL
- VB, C#, Delphi, and other COM-capable environments through COM
- Windows 7 or later
- asynchronous TR request/response handling
- DeView for TR inspection and testing

Because the NoSlip development host may be macOS or Linux, the integration uses
a small token-protected bridge that runs next to the official module on
Windows. The bridge binds specifically to `127.0.0.1` and currently exposes:

- bridge/driver status
- Korean stock current price via official TR `300001`
- holdings via official TR `202021`
- mock responses for local development

Live Yuanta orders are intentionally not exposed. The official order TRs
contain account passwords and require Windows-side simulation testing,
operational controls, and independent review before a submission endpoint is
considered.

### Windows Bridge

1. Apply for Yuanta Open API and simulation access on the official site.
2. Install the official `YuantaAPI` package and confirm TRs in DeView.
3. Create a Windows Python environment and install requirements.
4. Keep the following values only on the Windows host:

```bash
YUANTA_BRIDGE_DRIVER=com
YUANTA_BRIDGE_TOKEN=at-least-32-random-characters
YUANTA_COM_SERVER=simul.tradar.api.com
YUANTA_COM_API_PATH=C:\path\to\YuantaAPI
YUANTA_USER_ID=...
YUANTA_USER_PASSWORD=...
YUANTA_CERT_PASSWORD=...
YUANTA_ACCOUNT_NUMBER=...
YUANTA_ACCOUNT_AID=...
```

5. Start the loopback bridge from the repository root:

```bash
python -m services.trader.brokers.yuanta_bridge
```

For a NoSlip process on another machine, do not expose port `8765` to the LAN.
Use an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 windows-user@windows-host
```

Then keep `YUANTA_BRIDGE_URL=http://127.0.0.1:8765` in NoSlip.

### Mock Bridge

The mock driver can run on any OS and never connects to an account:

```bash
YUANTA_BRIDGE_DRIVER=mock
YUANTA_BRIDGE_TOKEN=at-least-32-random-characters
YUANTA_MOCK_PRICES_JSON='{"005930":"72000"}'
python -m services.trader.brokers.yuanta_bridge
```

## MCP Tools

- `get_broker_status`
- `get_broker_prices`
- `get_broker_holdings`
- `prepare_broker_order`

## Telegram Commands

- `/broker toss`
- `/broker yuanta`
- `/quote toss 005930 AAPL`
- `/quote yuanta 005930`
- `/holdings toss`
- `/holdings yuanta`

Never send a client secret, password, certificate password, account password,
private key, seed phrase, or exchange API key through Telegram or an MCP prompt.

## Tests

```bash
services/trader/.venv/bin/python -m unittest discover \
  -s services/trader/tests -v
```

These tests use fake transports and do not contact either brokerage.
