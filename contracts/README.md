# NoSlip Web3 Contract Drafts

These contracts are conservative Base/EVM-compatible drafts for local and Base
Sepolia testing. They are not approved for mainnet deployment.

- `NoSlipCreditsReceipt.sol` emits optional proof-of-purchase receipts.
- `NoSlipAccessPass.sol` is a non-transferable product access pass.
- `NSQToken.sol` is a capped, pausable future utility-token placeholder.

NSQ does not provide yield, profit sharing, guaranteed returns, or automatic
trading-profit distribution. Legal and independent security review are required
before any deployment or distribution.

## Install and test

Run these commands from the repository root:

```bash
npm --prefix contracts install
npm --prefix contracts run compile
npm --prefix contracts test
```

## Local deployment

Run these commands from `contracts/` in separate terminals:

```bash
npm run node
npm run deploy:local
```

## Base Sepolia deployment only

Store deployment secrets through the Hardhat keystore or environment-backed
configuration variables. Never commit a private key.

```bash
npx hardhat keystore set BASE_SEPOLIA_RPC_URL
npx hardhat keystore set BASE_SEPOLIA_PRIVATE_KEY
npm run deploy:base-sepolia
```

Do not configure or deploy to Base mainnet until legal, security, operational,
and payment-verification reviews are complete.
