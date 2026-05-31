import { predictSymbol } from '@/app/actions/prediction';
import { guardApiRequest, secureJson } from '@/app/api/_lib/security';
import { deductUserCredits, getUserCredits } from '@/app/actions/mockDb';

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

  try {
    const { symbol } = await request.json();

    if (!symbol) {
      return secureJson(
        { error: 'Missing required symbol' },
        { status: 400 }
      );
    }

    // Check credits before executing calculation
    const currentCredits = await getUserCredits('default-saas-user');
    if (currentCredits < 10) {
      return secureJson(
        { error: 'Insufficient credits. 10 credits required.' },
        { status: 402 } // Payment Required
      );
    }

    // Deduct 10 credits
    const success = await deductUserCredits('default-saas-user', 10);
    if (!success) {
      return secureJson(
        { error: 'Insufficient credits. 10 credits required.' },
        { status: 402 }
      );
    }

    const result = await predictSymbol(symbol);

    if (!result) {
      return secureJson(
        { error: 'Prediction failed' },
        { status: 500 }
      );
    }

    const remainingCredits = await getUserCredits('default-saas-user');
    return secureJson({ ...result, remainingCredits });
  } catch (error) {
    console.error('[Predict-Symbol] Error:', error);
    return secureJson(
      { error: 'Internal error' },
      { status: 500 }
    );
  }
}

