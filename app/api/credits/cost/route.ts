import { secureJson } from "@/app/api/_lib/security";
import {
  estimateFeatureCost,
  FEATURE_COSTS,
  isPremiumFeature,
} from "@/server/featureGate";

export async function GET(request: Request) {
  const feature = new URL(request.url).searchParams.get("feature");
  if (feature) {
    if (!isPremiumFeature(feature)) {
      return secureJson(
        { ok: false, error: "Unknown premium feature" },
        { status: 400 }
      );
    }
    return secureJson({
      ok: true,
      feature,
      cost: estimateFeatureCost(feature),
    });
  }
  return secureJson({
    ok: true,
    costs: FEATURE_COSTS,
  });
}
