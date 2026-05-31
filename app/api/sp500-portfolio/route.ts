import { buildSp500Portfolio } from "@/app/actions/prediction";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "sp500-portfolio",
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "sp500-portfolio",
      limit: 10,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const payload = await request.json().catch(() => ({}));
    const result = await buildSp500Portfolio(Boolean(payload?.forceRefresh));

    if (!result.ok) {
      return secureJson(
        { error: result.error || "Failed to build S&P500 portfolio." },
        { status: 500 }
      );
    }

    return secureJson(result);
  } catch (error) {
    console.error("[S&P500 Portfolio] Error:", error);
    return secureJson(
      { error: "Internal error" },
      { status: 500 }
    );
  }
}
