# NoSlip Securities Integration Security

## Threat Model

Primary risks:

- brokerage client secret, login password, certificate password, or account
  password disclosure
- accidental or duplicated live orders
- an LLM, Telegram user, or hosted API triggering an order without explicit
  human confirmation
- exposing the Yuanta Windows COM bridge to a LAN or the public internet
- OAuth token churn invalidating another Toss client process
- untrusted custom API hosts collecting credentials
- account and holdings data leaking through logs
- stale vendor modules or undocumented TR changes causing incorrect behavior

## Implemented Controls

- Both providers default to `disabled`.
- Toss read operations require explicit `read_only`, `paper`, or `live` mode.
- Toss uses only the official HTTPS host unless custom-host opt-in is explicit.
- Custom Toss hosts must use HTTPS, except loopback HTTP for tests.
- Toss OAuth tokens are cached in memory and never written to disk.
- Toss live orders require mode, allow-live flag, and exact runtime
  confirmation.
- MCP and Telegram cannot submit, modify, or cancel securities orders.
- Order previews validate symbols, positive decimals, LIMIT/MARKET rules, and
  mutually exclusive quantity/amount inputs.
- Yuanta credentials exist only on the Windows bridge host.
- The Yuanta bridge binds specifically to `127.0.0.1` and requires a 32+
  character bearer token.
- Broker HTTP clients reject automatic redirects so credentials are not
  forwarded through an unexpected 307/308 response.
- Remote Yuanta access must use SSH port forwarding; direct LAN/public binding
  is rejected.
- Yuanta live order submission is not implemented.
- Bridge errors do not include request bodies, passwords, tokens, or account
  values.
- Yuanta account numbers returned by the COM driver are masked.

## Credential Policy

Never commit or upload:

- `TOSS_SECURITIES_CLIENT_SECRET`
- `YUANTA_BRIDGE_TOKEN`
- `YUANTA_USER_PASSWORD`
- `YUANTA_CERT_PASSWORD`
- `YUANTA_ACCOUNT_NUMBER`
- `YUANTA_ACCOUNT_AID`
- any brokerage account password

Do not accept these values through a browser form, Telegram, MCP prompt, issue,
chat, or support ticket. Store production secrets in an approved secret manager
or protected host environment. Rotate a value immediately if it appears in a
log, commit, screenshot, or message.

## Deployment Policy

Toss:

- Start with `read_only`.
- Confirm account selection and rate-limit behavior.
- Keep live order flags false until paper procedures, monitoring,
  reconciliation, and incident response are documented.
- Use a single token-owning process or coordinated token cache because token
  reissuance invalidates the previous token.

Yuanta:

- Download the COM/DLL package only from the official MyAsset page.
- Validate package updates and TR definitions in DeView.
- Use `simul.tradar.api.com` and Yuanta simulation before production review.
- Run the bridge under a dedicated Windows user.
- Block inbound firewall access to the bridge port.
- Use SSH forwarding for cross-host access.
- Do not add live order endpoints without code review, replay protection,
  per-order human confirmation, limits, kill switch, and audit logging.

## Verification Checklist

- [x] Providers disabled by default.
- [x] No brokerage credentials committed to source.
- [x] Toss official OAuth2 and account header implemented.
- [x] Toss token cache implemented.
- [x] Toss live submission protected by three independent gates.
- [x] Toss order idempotency key supported.
- [x] Yuanta official COM/DLL architecture documented.
- [x] Yuanta bridge restricted to loopback and bearer authentication.
- [x] Yuanta simulation server is the documented default.
- [x] MCP and Telegram are read/preview only.
- [x] Unit tests do not contact live brokerage services.
- [ ] Toss live account smoke test completed by the account owner.
- [ ] Yuanta Windows COM simulation test completed by the account owner.
- [ ] Independent review completed before enabling any live order path.

## Disclaimer

NoSlip provides analytics and integration software only. It is not financial
advice, investment advice, or a licensed broker/dealer service. Trading
performance is not guaranteed. Users are responsible for account security,
order review, and their own decisions.
