import { secureJson } from '@/app/api/_lib/security';
import { getUserProfile, initializeUserIfMissing } from '@/app/actions/mockDb';

export async function GET() {
  try {
    const profile = await getUserProfile('default-saas-user');
    return secureJson(profile);
  } catch (error) {
    console.error('[User-Profile] GET error:', error);
    return secureJson(
      { error: 'Failed to retrieve profile' },
      { status: 500 }
    );
  }
}

// Allow POST to initialize or modify profile directly for manual testing convenience
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const userId = body.userId || 'default-saas-user';
    const profile = await initializeUserIfMissing(userId);
    return secureJson(profile);
  } catch (error) {
    console.error('[User-Profile] POST error:', error);
    return secureJson(
      { error: 'Failed to initialize profile' },
      { status: 500 }
    );
  }
}
