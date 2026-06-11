import { secureJson } from '@/app/api/_lib/security';
import { getUserProfile, initializeUserIfMissing } from '@/app/actions/mockDb';
import {
  ApiAuthenticationError,
  resolveRequestIdentity,
} from '@/app/api/_lib/auth';

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const identity = resolveRequestIdentity(request, {
      requestedUserId: url.searchParams.get('userId'),
    });
    const profile = await getUserProfile(identity.userId);
    return secureJson(profile);
  } catch (error) {
    console.error('[User-Profile] GET error:', error);
    return secureJson(
      { error: 'Failed to retrieve profile' },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}

// Allow POST to initialize or modify profile directly for manual testing convenience
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const identity = resolveRequestIdentity(request, {
      requestedUserId: body.userId,
    });
    const profile = await initializeUserIfMissing(identity.userId);
    return secureJson(profile);
  } catch (error) {
    console.error('[User-Profile] POST error:', error);
    return secureJson(
      { error: 'Failed to initialize profile' },
      { status: error instanceof ApiAuthenticationError ? 401 : 500 }
    );
  }
}
