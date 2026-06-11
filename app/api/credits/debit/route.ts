import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from "@/app/api/_lib/auth";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import {
  InsufficientCreditsError,
  isPremiumFeature,
  requireCredits,
} from "@/server/featureGate";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "credits-debit",
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "credits-debit",
      limit: 60,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const body = await request.json();
    if (!isPremiumFeature(body.feature)) {
      return secureJson(
        { ok: false, error: "Unknown premium feature" },
        { status: 400 }
      );
    }
    const identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
      requestedWalletAddress: body.walletAddress,
    });
    const result = await requireCredits({
      userId: identity.userId,
      walletAddress: identity.walletAddress,
      feature: body.feature,
      metadata: {
        source: identity.source,
      },
    });
    return secureJson({
      ok: true,
      feature: body.feature,
      debited: result.required,
      balance: result.balance,
      transactionId: result.transaction.id,
    });
  } catch (error) {
    if (error instanceof InsufficientCreditsError) {
      return secureJson(
        {
          ok: false,
          error: error.code,
          required: error.required,
          balance: error.balance,
        },
        { status: 402 }
      );
    }
    return secureJson(
      {
        ok: false,
        error: error instanceof Error ? error.message : "Unable to debit credits",
      },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
