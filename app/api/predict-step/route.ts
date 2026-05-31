import { predictStep } from '@/app/actions/prediction';
import { guardApiRequest, isSp500OnlyRequest, secureJson } from '@/app/api/_lib/security';

export async function POST(request: Request) {
  if (isSp500OnlyRequest(request)) {
    return secureJson(
      { error: 'Crypto route prediction is disabled in local S&P500-only mode.' },
      { status: 404 }
    );
  }

  const guard = guardApiRequest(request, {
    routeKey: 'predict-step',
    maxBodyBytes: 8 * 1024,
    allowedContentTypes: ['application/json'],
    rateLimit: {
      key: 'predict-step',
      limit: 24,
      windowMs: 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const { inputMint, outputMint, symbol } = await request.json();

    if (!inputMint || !outputMint || !symbol) {
      return secureJson(
        { error: 'Missing required fields' },
        { status: 400 }
      );
    }

    const result = await predictStep(inputMint, outputMint, symbol);

    if (!result) {
      return secureJson(
        { error: 'Prediction failed' },
        { status: 500 }
      );
    }

    return secureJson(result);
  } catch (error) {
    console.error('[Predict-Step] Error:', error);
    return secureJson(
      { error: 'Internal error' },
      { status: 500 }
    );
  }
}
