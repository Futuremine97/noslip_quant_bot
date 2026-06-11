import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from "@/app/api/_lib/auth";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import {
  getCreditAccount,
  getCreditTransactions,
} from "@/server/credits";

export async function GET(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "credits-balance",
    maxBodyBytes: 0,
    rateLimit: {
      key: "credits-balance",
      limit: 120,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const url = new URL(request.url);
    const identity = resolveRequestIdentity(request, {
      requestedUserId: url.searchParams.get("userId"),
    });
    const [account, transactions] = await Promise.all([
      getCreditAccount(identity.userId, identity.walletAddress),
      getCreditTransactions(identity.userId, 20),
    ]);
    return secureJson({
      ok: true,
      account,
      transactions,
    });
  } catch (error) {
    return secureJson(
      {
        ok: false,
        error:
          error instanceof Error ? error.message : "Unable to read credits",
      },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
