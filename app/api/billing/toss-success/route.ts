import { createHash } from "node:crypto";

import { NextResponse } from 'next/server';
import { addUserCredits, updateUserPlan } from '@/app/actions/mockDb';
import { guardApiRequest } from '@/app/api/_lib/security';
import { resolveTossPurchase } from '@/server/billingCatalog';

type TossConfirmation = {
  orderId?: string;
  paymentKey?: string;
  status?: string;
  totalAmount?: number;
  code?: string;
  message?: string;
};

function paymentRedirect(
  request: Request,
  state: "success" | "fail",
  params: Record<string, string>
) {
  const url = new URL("/", request.url);
  url.searchParams.set("payment", state);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, value);
  }
  return NextResponse.redirect(url);
}

export async function GET(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "toss-payment-success",
    maxBodyBytes: 0,
    rateLimit: {
      key: "toss-payment-success",
      limit: 30,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const { searchParams } = new URL(request.url);
    const paymentKey = searchParams.get('paymentKey');
    const orderId = searchParams.get('orderId');
    const amountRaw = searchParams.get('amount');

    if (!paymentKey || !orderId || !amountRaw) {
      return paymentRedirect(request, "fail", {
        code: "INVALID_PARAMS",
        message: "Missing required payment parameters",
      });
    }

    const amount = Number(amountRaw);
    const purchase = resolveTossPurchase(orderId, amount);
    const secretKey = String(process.env.TOSS_SECRET_KEY || '').trim();
    if (!secretKey) {
      throw new Error("Toss Payments is not configured");
    }
    const basicAuth = Buffer.from(`${secretKey}:`).toString('base64');

    const confirmResponse = await fetch('https://api.tosspayments.com/v1/payments/confirm', {
      method: 'POST',
      headers: {
        Authorization: `Basic ${basicAuth}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ paymentKey, orderId, amount }),
      cache: "no-store",
    });

    const responseBody = await confirmResponse.json() as TossConfirmation;

    if (!confirmResponse.ok) {
      console.error('[Toss Success Callback] Confirmation failed:', {
        status: confirmResponse.status,
        code: responseBody.code,
      });
      return paymentRedirect(request, "fail", {
        code: "CONFIRM_FAILED",
        message: responseBody.message || "Payment confirmation failed",
      });
    }

    if (
      responseBody.orderId !== orderId ||
      responseBody.paymentKey !== paymentKey ||
      responseBody.status !== "DONE" ||
      Number(responseBody.totalAmount) !== purchase.amountKrw
    ) {
      console.error('[Toss Success Callback] Confirmation mismatch:', {
        orderMatches: responseBody.orderId === orderId,
        paymentKeyMatches: responseBody.paymentKey === paymentKey,
        status: responseBody.status,
        amountMatches: Number(responseBody.totalAmount) === purchase.amountKrw,
      });
      return paymentRedirect(request, "fail", {
        code: "CONFIRM_MISMATCH",
        message: "Payment confirmation did not match the requested purchase",
      });
    }

    const paymentKeyHash = createHash("sha256")
      .update(paymentKey)
      .digest("hex");
    const idempotencyKey = `toss-payment:${paymentKeyHash}`;
    const metadata = {
      paymentProvider: "toss-payments",
      paymentKeyHash,
      orderId,
      amountKrw: purchase.amountKrw,
    };
    if (purchase.type === 'plan') {
      await updateUserPlan(purchase.userId, purchase.value, {
        idempotencyKey: `${idempotencyKey}:plan-bonus`,
        reason: `payment:toss:plan:${purchase.value}`,
        metadata,
      });
    } else {
      await addUserCredits(purchase.userId, purchase.creditAmount, {
        idempotencyKey: `${idempotencyKey}:credits`,
        reason: "payment:toss:credits",
        metadata,
      });
    }

    console.info('[Toss Success Callback] Purchase applied:', {
      type: purchase.type,
      value: purchase.value,
      userId: purchase.userId,
    });
    return paymentRedirect(request, "success", {
      type: purchase.type,
      value: purchase.value,
    });
  } catch (error) {
    console.error(
      '[Toss Success Callback] Request failed:',
      error instanceof Error ? error.message : 'Unknown error'
    );
    return paymentRedirect(request, "fail", {
      code: "SERVER_ERROR",
      message: error instanceof Error ? error.message : "Internal server error",
    });
  }
}
