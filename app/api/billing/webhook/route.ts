import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from '@/app/api/_lib/auth';
import { guardApiRequest, secureJson } from '@/app/api/_lib/security';
import { updateUserPlan } from '@/app/actions/mockDb';
import { getPaymentMode } from '@/lib/web3/basePayment';

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: "legacy-billing-simulation",
    maxBodyBytes: 2 * 1024,
    allowedContentTypes: ["application/json"],
    rateLimit: {
      key: "legacy-billing-simulation",
      limit: 10,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  if (process.env.NODE_ENV === "production" || getPaymentMode() !== "mock") {
    return secureJson(
      { ok: false, error: "Billing simulation is disabled" },
      { status: 404 }
    );
  }

  try {
    const body = await request.json();
    if (body.type !== "plan.selected" || body.plan !== "basic") {
      return secureJson(
        {
          ok: false,
          error: "Only local Basic plan selection is supported by this endpoint",
        },
        { status: 400 }
      );
    }

    const identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
    });
    const profile = await updateUserPlan(identity.userId, "basic");

    return secureJson({
      ok: true,
      plan: profile.plan,
      credits: profile.credits,
    });
  } catch (error) {
    console.error(
      '[Billing Simulation] Request failed:',
      error instanceof Error ? error.message : 'Unknown error'
    );
    return secureJson(
      { ok: false, error: 'Failed to update the local plan' },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
