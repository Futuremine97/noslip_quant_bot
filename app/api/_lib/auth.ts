import { timingSafeEqual } from "node:crypto";

import { isAddress } from "viem";

import {
  isLocalDevelopmentRequest,
  readWalletSessionToken,
  WALLET_SESSION_COOKIE,
} from "@/server/web3Auth";

export type RequestIdentity = {
  userId: string;
  walletAddress?: string;
  source: "wallet" | "api-token" | "local";
};

export class ApiAuthenticationError extends Error {
  constructor(message = "Authentication required") {
    super(message);
    this.name = "ApiAuthenticationError";
  }
}

function safeEqual(left: string, right: string) {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return (
    leftBuffer.length === rightBuffer.length &&
    timingSafeEqual(leftBuffer, rightBuffer)
  );
}

function getBearerToken(request: Request) {
  const authorization = request.headers.get("authorization") || "";
  return authorization.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length).trim()
    : "";
}

function hasValidApiToken(request: Request) {
  const expected = String(
    process.env.NOSLIP_API_TOKEN || process.env.PREDICTION_API_TOKEN || ""
  ).trim();
  const provided = getBearerToken(request);
  return Boolean(expected && provided && safeEqual(expected, provided));
}

function normalizeUserId(value?: string | null) {
  const userId = String(value || "").trim();
  if (!userId || userId.length > 160 || !/^[A-Za-z0-9:_.@-]+$/.test(userId)) {
    throw new ApiAuthenticationError("Invalid user identity");
  }
  return userId;
}

function normalizeWallet(value?: string | null) {
  const wallet = String(value || "").trim();
  if (!wallet) {
    return undefined;
  }
  if (!isAddress(wallet)) {
    throw new ApiAuthenticationError("Invalid wallet address");
  }
  return wallet.toLowerCase();
}

export function resolveRequestIdentity(
  request: Request,
  options: {
    requestedUserId?: string | null;
    requestedWalletAddress?: string | null;
  } = {}
): RequestIdentity {
  const cookieToken = request.headers
    .get("cookie")
    ?.split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${WALLET_SESSION_COOKIE}=`))
    ?.slice(WALLET_SESSION_COOKIE.length + 1);
  const walletSession = readWalletSessionToken(cookieToken);
  if (walletSession) {
    return {
      userId: walletSession.userId,
      walletAddress: walletSession.walletAddress,
      source: "wallet",
    };
  }

  if (hasValidApiToken(request)) {
    return {
      userId: normalizeUserId(options.requestedUserId || "api:default"),
      ...(normalizeWallet(options.requestedWalletAddress)
        ? {
            walletAddress: normalizeWallet(options.requestedWalletAddress),
          }
        : {}),
      source: "api-token",
    };
  }

  if (isLocalDevelopmentRequest(request)) {
    return {
      userId: normalizeUserId(
        options.requestedUserId ||
          request.headers.get("x-noslip-user-id") ||
          "local:browser"
      ),
      ...(normalizeWallet(options.requestedWalletAddress)
        ? {
            walletAddress: normalizeWallet(options.requestedWalletAddress),
          }
        : {}),
      source: "local",
    };
  }

  throw new ApiAuthenticationError();
}
