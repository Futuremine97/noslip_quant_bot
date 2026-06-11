import type { NextRequest } from "next/server";

import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import {
  createMockWalletSession,
  createWalletSessionToken,
  readWalletSessionToken,
  verifyWalletChallenge,
  WALLET_CHALLENGE_COOKIE,
  WALLET_SESSION_COOKIE,
} from "@/server/web3Auth";

const SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

export async function GET(request: NextRequest) {
  const session = readWalletSessionToken(
    request.cookies.get(WALLET_SESSION_COOKIE)?.value
  );
  return secureJson({
    ok: true,
    authenticated: Boolean(session),
    session,
  });
}

export async function POST(request: NextRequest) {
  const guard = guardApiRequest(request, {
    routeKey: "web3-session",
    maxBodyBytes: 8 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "web3-session",
      limit: 20,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const body = await request.json();
    const session = body.mock
      ? createMockWalletSession(request)
      : await verifyWalletChallenge({
          challengeToken:
            request.cookies.get(WALLET_CHALLENGE_COOKIE)?.value || "",
          address: body.address,
          message: body.message,
          signature: body.signature,
        });
    const response = secureJson({
      ok: true,
      authenticated: true,
      session,
    });
    response.cookies.set(
      WALLET_SESSION_COOKIE,
      createWalletSessionToken(session),
      {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
        path: "/",
        maxAge: SESSION_MAX_AGE_SECONDS,
      }
    );
    response.cookies.delete(WALLET_CHALLENGE_COOKIE);
    return response;
  } catch (error) {
    return secureJson(
      {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : "Wallet authentication failed",
      },
      { status: 401 }
    );
  }
}

export async function DELETE(request: NextRequest) {
  const guard = guardApiRequest(request, {
    routeKey: "web3-session-delete",
    maxBodyBytes: 0,
    rateLimit: {
      key: "web3-session-delete",
      limit: 30,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  const response = secureJson({ ok: true, authenticated: false });
  response.cookies.delete(WALLET_SESSION_COOKIE);
  response.cookies.delete(WALLET_CHALLENGE_COOKIE);
  return response;
}
