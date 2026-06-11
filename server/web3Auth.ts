import {
  createHmac,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";

import { isAddress, verifyMessage } from "viem";

import { getPaymentChain } from "@/lib/web3/basePayment";

export const WALLET_CHALLENGE_COOKIE = "noslip_wallet_challenge";
export const WALLET_SESSION_COOKIE = "noslip_wallet_session";

const CHALLENGE_TTL_MS = 5 * 60 * 1000;
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

type SignedEnvelope<T> = {
  payload: T;
  signature: string;
};

type WalletChallenge = {
  address: `0x${string}`;
  chainId: number;
  nonce: string;
  message: string;
  expiresAt: number;
};

export type WalletSession = {
  userId: string;
  walletAddress: `0x${string}`;
  chainId: number;
  expiresAt: number;
  mode: "wallet" | "mock";
};

function getSessionSecret() {
  const configured = String(process.env.NOSLIP_SESSION_SECRET || "").trim();
  if (configured.length >= 32) {
    return configured;
  }
  if (process.env.NODE_ENV !== "production") {
    return "noslip-local-development-session-secret-change-me";
  }
  throw new Error(
    "NOSLIP_SESSION_SECRET must contain at least 32 characters in production"
  );
}

function signPayload(payload: unknown) {
  const encodedPayload = Buffer.from(JSON.stringify(payload)).toString(
    "base64url"
  );
  const signature = createHmac("sha256", getSessionSecret())
    .update(encodedPayload)
    .digest("base64url");
  return Buffer.from(
    JSON.stringify({ payload: encodedPayload, signature })
  ).toString("base64url");
}

function verifySignedPayload<T>(token: string): T | null {
  try {
    const envelope = JSON.parse(
      Buffer.from(token, "base64url").toString("utf8")
    ) as SignedEnvelope<string>;
    const expected = createHmac("sha256", getSessionSecret())
      .update(envelope.payload)
      .digest();
    const provided = Buffer.from(envelope.signature, "base64url");
    if (
      expected.length !== provided.length ||
      !timingSafeEqual(expected, provided)
    ) {
      return null;
    }
    return JSON.parse(
      Buffer.from(envelope.payload, "base64url").toString("utf8")
    ) as T;
  } catch {
    return null;
  }
}

export function isLocalDevelopmentRequest(request: Request) {
  if (process.env.NODE_ENV === "production") {
    return false;
  }
  try {
    return LOCAL_HOSTS.has(new URL(request.url).hostname);
  } catch {
    return false;
  }
}

export function createWalletChallenge(request: Request, addressValue: string) {
  if (!isAddress(addressValue)) {
    throw new Error("Invalid EVM wallet address");
  }

  const address = addressValue.toLowerCase() as `0x${string}`;
  const chain = getPaymentChain();
  const nonce = randomBytes(18).toString("base64url");
  const expiresAt = Date.now() + CHALLENGE_TTL_MS;
  const host = new URL(request.url).host;
  const message = [
    "NoSlip Quant wallet sign-in",
    "",
    `Domain: ${host}`,
    `Address: ${address}`,
    `Chain: ${chain.displayName} (${chain.chainId})`,
    `Nonce: ${nonce}`,
    `Expires: ${new Date(expiresAt).toISOString()}`,
    "",
    "This signature authenticates access only. It does not authorize a transaction.",
  ].join("\n");
  const challenge: WalletChallenge = {
    address,
    chainId: chain.chainId,
    nonce,
    message,
    expiresAt,
  };

  return {
    challenge,
    token: signPayload(challenge),
    chain,
  };
}

export async function verifyWalletChallenge(options: {
  challengeToken: string;
  address: string;
  message: string;
  signature: string;
}): Promise<WalletSession> {
  const challenge = verifySignedPayload<WalletChallenge>(
    options.challengeToken
  );
  if (!challenge || challenge.expiresAt <= Date.now()) {
    throw new Error("Wallet challenge is missing or expired");
  }
  if (
    !isAddress(options.address) ||
    options.address.toLowerCase() !== challenge.address ||
    options.message !== challenge.message
  ) {
    throw new Error("Wallet challenge does not match");
  }

  const valid = await verifyMessage({
    address: challenge.address,
    message: challenge.message,
    signature: options.signature as `0x${string}`,
  });
  if (!valid) {
    throw new Error("Wallet signature verification failed");
  }

  return {
    userId: `wallet:${challenge.address}`,
    walletAddress: challenge.address,
    chainId: challenge.chainId,
    expiresAt: Date.now() + SESSION_TTL_MS,
    mode: "wallet",
  };
}

export function createMockWalletSession(request: Request): WalletSession {
  if (!isLocalDevelopmentRequest(request)) {
    throw new Error("Mock wallet sessions are local-development only");
  }
  const chain = getPaymentChain();
  const walletAddress =
    "0x0000000000000000000000000000000000084532" as const;
  return {
    userId: `wallet:${walletAddress}`,
    walletAddress,
    chainId: chain.chainId,
    expiresAt: Date.now() + SESSION_TTL_MS,
    mode: "mock",
  };
}

export function createWalletSessionToken(session: WalletSession) {
  return signPayload(session);
}

export function readWalletSessionToken(token?: string | null) {
  if (!token) {
    return null;
  }
  const session = verifySignedPayload<WalletSession>(token);
  if (
    !session ||
    session.expiresAt <= Date.now() ||
    !isAddress(session.walletAddress)
  ) {
    return null;
  }
  return session;
}
