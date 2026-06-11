import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from "@/app/api/_lib/auth";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import { paymentReceiptStore } from "@/server/paymentReceipts";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "payment-confirm",
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "payment-confirm",
      limit: 20,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const body = await request.json();
    if (
      typeof body.intentId !== "string" ||
      body.intentId.length < 10 ||
      body.intentId.length > 100
    ) {
      return secureJson(
        { ok: false, error: "Invalid payment intent ID" },
        { status: 400 }
      );
    }
    const identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
    });
    const result = await paymentReceiptStore.confirmIntent({
      intentId: body.intentId,
      userId: identity.userId,
      txHash: body.txHash,
    });
    return secureJson({
      ok: true,
      intent: result.intent,
      balance: result.balance,
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to confirm payment";
    const status =
      error instanceof ApiAuthenticationError
        ? 401
        : message.includes("not found")
          ? 404
          : message.includes("not implemented")
            ? 501
            : 400;
    return secureJson({ ok: false, error: message }, { status });
  }
}
