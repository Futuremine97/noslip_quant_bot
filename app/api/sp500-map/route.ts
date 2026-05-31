import { buildSp500InformationMap } from "@/app/actions/prediction";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "sp500-map",
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "sp500-map",
      limit: 10,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const payload = await request.json().catch(() => ({}));
    const result = await buildSp500InformationMap(Boolean(payload?.forceRefresh));

    if (!result.ok) {
      return secureJson(
        { error: result.error || "Failed to build S&P500 information map." },
        { status: 500 }
      );
    }

    return secureJson(result);
  } catch (error) {
    console.error("[S&P500 Map] Error:", error);
    return secureJson(
      { error: "Internal error" },
      { status: 500 }
    );
  }
}
