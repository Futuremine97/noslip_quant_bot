import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from "@/app/api/_lib/auth";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import { getPaymentMode } from "@/lib/web3/basePayment";
import { grantCredits } from "@/server/credits";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "credits-grant",
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "credits-grant",
      limit: 10,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  if (process.env.NODE_ENV === "production" || getPaymentMode() !== "mock") {
    return secureJson(
      { ok: false, error: "Development credit grants are disabled" },
      { status: 404 }
    );
  }

  try {
    const body = await request.json();
    const amount = Number(body.amount ?? 100);
    if (!Number.isSafeInteger(amount) || amount < 1 || amount > 1000) {
      return secureJson(
        { ok: false, error: "Grant amount must be an integer from 1 to 1000" },
        { status: 400 }
      );
    }
    const identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
      requestedWalletAddress: body.walletAddress,
    });
    const result = await grantCredits({
      userId: identity.userId,
      walletAddress: identity.walletAddress,
      amount,
      reason: "development_grant",
      metadata: {
        source: identity.source,
      },
    });
    return secureJson({
      ok: true,
      granted: amount,
      balance: result.account.balance,
      transactionId: result.transaction.id,
    });
  } catch (error) {
    return secureJson(
      {
        ok: false,
        error: error instanceof Error ? error.message : "Unable to grant credits",
      },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
