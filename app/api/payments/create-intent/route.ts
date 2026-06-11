import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from "@/app/api/_lib/auth";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";
import {
  getPaymentMode,
  isCreditPackageId,
} from "@/lib/web3/basePayment";
import { paymentReceiptStore } from "@/server/paymentReceipts";

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "payment-create-intent",
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "payment-create-intent",
      limit: 20,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const body = await request.json();
    if (!isCreditPackageId(body.packageId)) {
      return secureJson(
        { ok: false, error: "Unknown credit package" },
        { status: 400 }
      );
    }
    const identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
      requestedWalletAddress: body.walletAddress,
    });
    const intent = await paymentReceiptStore.createIntent({
      userId: identity.userId,
      walletAddress: identity.walletAddress,
      packageId: body.packageId,
    });
    return secureJson({
      ok: true,
      paymentMode: getPaymentMode(),
      intent,
    });
  } catch (error) {
    return secureJson(
      {
        ok: false,
        error:
          error instanceof Error ? error.message : "Unable to create payment intent",
      },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
