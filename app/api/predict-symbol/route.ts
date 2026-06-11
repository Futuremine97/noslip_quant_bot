import { predictSymbol } from '@/app/actions/prediction';
import {
  ApiAuthenticationError,
  resolveRequestIdentity,
  type RequestIdentity,
} from '@/app/api/_lib/auth';
import { guardApiRequest, secureJson } from '@/app/api/_lib/security';
import { grantCredits } from '@/server/credits';
import {
  InsufficientCreditsError,
  requireCredits,
} from '@/server/featureGate';
import type { CreditTransaction } from '@/server/creditLedger';

export async function POST(request: Request) {
  const guard = guardApiRequest(request, {
    routeKey: 'predict-symbol',
    maxBodyBytes: 4 * 1024,
    allowedContentTypes: ['application/json'],
    rateLimit: {
      key: 'predict-symbol',
      limit: 24,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  let identity: RequestIdentity | null = null;
  let debitTransaction: CreditTransaction | null = null;

  try {
    const body = await request.json();
    const { symbol } = body;
    if (!symbol) {
      return secureJson(
        { error: 'Missing required symbol' },
        { status: 400 }
      );
    }

    identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
    });
    const debit = await requireCredits({
      userId: identity.userId,
      walletAddress: identity.walletAddress,
      feature: 'zero_shot_forecast',
      metadata: {
        symbol,
        source: identity.source,
      },
    });
    debitTransaction = debit.transaction;

    const result = await predictSymbol(symbol);

    if (!result) {
      throw new Error('Prediction failed');
    }

    return secureJson({ ...result, remainingCredits: debit.balance });
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

    if (identity && debitTransaction) {
      try {
        await grantCredits({
          userId: identity.userId,
          walletAddress: identity.walletAddress,
          amount: Math.abs(debitTransaction.amount),
          reason: 'feature_refund:zero_shot_forecast',
          metadata: {
            debitTransactionId: debitTransaction.id,
          },
          idempotencyKey: `refund:${debitTransaction.id}`,
        });
      } catch (refundError) {
        console.error('[Predict-Symbol] Credit refund failed:', refundError);
      }
    }

    console.error('[Predict-Symbol] Error:', error);
    return secureJson(
      {
        error:
          error instanceof ApiAuthenticationError
            ? error.message
            : 'Internal error',
      },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
