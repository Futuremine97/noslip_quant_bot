import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from "@/app/api/_lib/auth";
import { secureJson } from "@/app/api/_lib/security";
import {
  checkPremiumAccess,
  isPremiumFeature,
} from "@/server/featureGate";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const feature = url.searchParams.get("feature");
    if (!isPremiumFeature(feature)) {
      return secureJson(
        { ok: false, error: "Unknown premium feature" },
        { status: 400 }
      );
    }
    const identity = resolveRequestIdentity(request, {
      requestedUserId: url.searchParams.get("userId"),
    });
    const access = await checkPremiumAccess({
      userId: identity.userId,
      walletAddress: identity.walletAddress,
      feature,
    });
    return secureJson({
      ok: true,
      feature,
      ...access,
    });
  } catch (error) {
    return secureJson(
      {
        ok: false,
        error:
          error instanceof Error ? error.message : "Unable to check access",
      },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
