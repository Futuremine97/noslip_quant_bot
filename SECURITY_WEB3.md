# NoSlip Web3 Security Checklist

## Scope

The Web3 layer is optional. Local analytics do not require a wallet, token, or
payment. The MVP uses off-chain credits; NSQ contracts are deployment drafts.

## Threat Model

Primary threats:

- forged wallet identity or replayed authentication signatures
- unauthorized credit grants, debits, or payment confirmations
- duplicate payment crediting
- tampered or fabricated Base transaction hashes
- exposure of private keys, seed phrases, API tokens, or uploaded datasets
- cross-site requests, oversized payloads, and endpoint abuse
- contract owner-key compromise or premature mainnet deployment
- legal or product copy that misrepresents NSQ as an investment

Controls implemented:

- wallet challenges expire after five minutes and are bound to address, chain,
  host, nonce, and exact message text
- signatures are verified server-side and create signed HttpOnly, SameSite
  session cookies
- hosted credit mutations require a wallet session or bearer API token
- localhost anonymous access and mock wallets are disabled in production
- feature costs are defined on the server, not supplied by clients
- file writes are serialized and atomic within one application process
- payment credit transactions use an idempotency key per payment intent
- development grants are disabled in production and outside mock mode
- Base mainnet intent creation requires a separate explicit opt-in
- API guards enforce origin checks, body limits, no-store headers, and basic
  rate limits

## Local vs Hosted Data Flow

Local development:

- `localhost` can use `local:browser` without wallet login.
- A clearly labeled demo wallet session is available only outside production.
- Credit/payment records are stored under `data/runtime/`, which is gitignored.
- `NOSLIP_PAYMENT_MODE=mock` confirms no on-chain transfer.

Hosted deployment:

- Configure `NOSLIP_SESSION_SECRET` with at least 32 random characters.
- Configure `NOSLIP_API_TOKEN` for MCP, Telegram, or service-to-service calls.
- Use HTTPS and an explicit `ALLOWED_APP_ORIGINS`/application origin.
- Use a durable transactional database instead of the JSON ledger for multiple
  instances or serverless concurrency.
- Treat uploaded CSV rows and prompts as data sent to the configured host.

## Private Key Policy

- No application route, MCP tool, Telegram command, or browser component accepts
  a private key or seed phrase.
- Users sign authentication messages and transactions in their own wallet.
- Never upload exchange API keys through the dashboard or Telegram.
- Contract deployer keys must be held in the Hardhat keystore or an approved
  external signer. Never commit them to source or `.env.example`.
- Rotate any token or key that is logged, committed, pasted into chat, or sent
  to an untrusted endpoint.

## Payment Verification Policy

Current mode:

- `NOSLIP_PAYMENT_MODE=mock`
- `NOSLIP_CHAIN_MODE=base-sepolia`
- confirmation produces a mock transaction hash and idempotently grants credits

Before production USDC or x402:

- verify chain ID and the canonical USDC contract address
- verify recipient, sender, amount, decimals, transaction success, and required
  confirmations against an independent Base RPC provider
- reject reused transaction hashes across all payment intents
- bind quoted amount and credit package to an expiring server-side intent
- handle reorgs, RPC disagreement, timeouts, and partial failures
- add database transactions around payment confirmation and credit issuance
- add webhook/request signature validation where applicable
- complete abuse, refund, accounting, and reconciliation procedures

## Smart Contract Deployment Policy

- Local Hardhat and Base Sepolia are the only approved targets for these drafts.
- Do not deploy to Base mainnet without independent contract audit, legal review,
  multisig/role design, monitoring, incident response, and verified source code.
- Keep owner powers minimal and document every privileged operation.
- Access passes are non-transferable product credentials.
- Credit receipts are proof events, not financial instruments.
- NSQ has no yield, profit sharing, guaranteed return, or trading-profit
  distribution behavior.

## Token Legal Risk Warning

NSQ is only a future utility-token design for analytics access, API usage,
signal-marketplace features, and creator reputation. Token classification,
distribution, marketing, geography, tax, sanctions, consumer protection, and
money-transmission issues require qualified legal review before launch.

## Testnet-First Checklist

- [x] Default chain is Base Sepolia.
- [x] Default payment mode is mock.
- [x] Mainnet mode requires explicit opt-in.
- [x] No private key exists in source code.
- [x] Wallet authentication uses a non-transaction signature.
- [x] Development credit grant is production-disabled.
- [x] Credit and payment operations have unit tests.
- [x] Draft contracts compile and have local tests.
- [ ] Resolve or replace audited Hardhat/Ignition development dependencies
  before any production deployment. Do not use `npm audit fix --force`
  without reviewing its breaking dependency changes.
- [ ] Production USDC/x402 verifier implemented and independently reviewed.
- [ ] Durable database and cross-instance transaction locking configured.
- [ ] Hosted deployment penetration test completed.
- [ ] Contract audit and legal review completed.

## Product Disclaimer

NoSlip is not financial advice, investment advice, or a licensed broker/dealer
service. It does not guarantee trading performance. Users are responsible for
their own decisions.
