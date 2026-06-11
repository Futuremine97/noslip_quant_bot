import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import {
  createWalletChallenge,
  WALLET_CHALLENGE_COOKIE,
} from "@/server/web3Auth";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "web3-challenge",
    maxBodyBytes: 2 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "web3-challenge",
      limit: 20,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const body = await request.json();
    const { challenge, token, chain } = createWalletChallenge(
      request,
      body.address
    );
    const response = secureJson({
      ok: true,
      message: challenge.message,
      chain,
      expiresAt: challenge.expiresAt,
    });
    response.cookies.set(WALLET_CHALLENGE_COOKIE, token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 5 * 60,
    });
    return response;
  } catch (error) {
    return secureJson(
      {
        ok: false,
        error: error instanceof Error ? error.message : "Invalid wallet request",
      },
      { status: 400 }
    );
  }
}
