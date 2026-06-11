# NoSlip Web3 Data Flow

## Architecture

```text
User / Telegram / MCP client / browser
        |
        v
NoSlip web app and authenticated API routes
        |
        +--> optional Base wallet session
        |
        +--> mock or verified USDC/x402 payment intent
        |
        v
Off-chain NoSlip Credit ledger
        |
        v
Premium forecast, whale, signal, tournament, and API features
        |
        v
Future optional NSQ utility and creator-reputation features
```

Wallet login and credits are optional. Local-only analytics can continue without
Web3 authentication.

## Local-Only Forecast

```text
CSV remains on the local machine
    -> local Python/model process
    -> local result and generated chart
```

The user should verify that the selected action actually invokes a local
service. A configured hosted URL changes this boundary.

## Hosted Forecast

```text
CSV rows or request inputs
    -> configured PREDICTION_API_URL
    -> hosted model execution
    -> response to the local app/client
```

The configured server can receive submitted data. Inspect the URL, operator,
retention policy, authentication, and TLS configuration before use.

## Wallet Login

```text
Browser requests wallet account
    -> app creates five-minute Base challenge
    -> wallet switches to configured Base chain
    -> user signs exact non-transaction message
    -> server verifies EIP-191 signature
    -> signed HttpOnly session cookie is created
```

The signature must not request token approval, asset transfer, or arbitrary
transaction execution. Local demo sessions are disabled in production.

## Credit Purchase

```text
User or agent requests server-priced package
    -> backend creates pending payment intent
    -> user pays USDC on Base/testnet from their wallet
    -> backend independently verifies transaction or x402 receipt
    -> intent becomes confirmed
    -> idempotent credit transaction is appended
```

The current implementation stops at mock confirmation. Production USDC/x402
verification is intentionally a documented TODO.

## Premium Feature

```text
Authenticated user requests premium feature
    -> server resolves wallet/API/local identity
    -> server reads the fixed feature cost
    -> atomic ledger debit succeeds or returns INSUFFICIENT_CREDITS
    -> feature executes
    -> failed execution receives an idempotent refund where integrated
```

Example insufficient-credit response:

```json
{
  "ok": false,
  "error": "INSUFFICIENT_CREDITS",
  "required": 3,
  "balance": 1
}
```

## MCP and Telegram

```text
MCP / Telegram command
    -> bearer-authenticated NoSlip API request
    -> balance, cost, access check, or payment intent
```

These clients never sign blockchain transactions and never request private keys,
seed phrases, or exchange API keys. `/pay` returns the configured dashboard URL
for user-controlled wallet interaction.

## Persistence Boundary

Local MVP records are stored in:

```text
data/runtime/web3-credit-ledger.json
data/runtime/web3-payment-intents.json
```

Writes are serialized and atomically renamed within one Node process. A hosted
multi-instance deployment must replace these files with a durable transactional
database and unique constraints for payment intent and transaction IDs.
