import { secureJson } from '@/app/api/_lib/security';
import { addUserCredits, updateUserPlan } from '@/app/actions/mockDb';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    console.log('[Billing Webhook] Received webhook payload:', body);

    // Support standard Stripe-like checkout.session.completed structure
    // or Toss Payments structure, or direct test payload.
    let userId = 'default-saas-user';
    let type = body.type || body.eventType || 'checkout.session.completed';
    let creditsToAdd = 0;
    let planToUpgrade: 'basic' | 'pro' | 'enterprise' | null = null;

    // Direct simulation payload
    if (body.userId) {
      userId = body.userId;
    }

    // Determine payload details
    if (type === 'checkout.session.completed' || type === 'payment.success') {
      // Look for custom metadata or simulated params
      const amountTotal = body.data?.object?.amount_total || body.amount || 10000; // default 10k KRW / $10
      
      // If payment is for a plan, map it
      const plan = body.plan || body.data?.object?.metadata?.plan;
      if (plan === 'pro' || plan === 'enterprise') {
        planToUpgrade = plan;
      } else {
        // Standard credit purchase: $10 = 1000 credits
        creditsToAdd = Math.floor(amountTotal / 10); // 1 credit per 10 KRW or cents
      }
    } else if (type === 'plan.subscribed') {
      planToUpgrade = body.plan || 'pro';
    } else if (type === 'credits.purchased') {
      creditsToAdd = body.credits || 500;
    } else {
      // Generic success fallback
      creditsToAdd = 1000;
    }

    let message = '';
    if (planToUpgrade) {
      const profile = await updateUserPlan(userId, planToUpgrade);
      message = `Successfully upgraded user ${userId} to plan: ${planToUpgrade}. Current credits: ${profile.credits}`;
    } else if (creditsToAdd > 0) {
      const finalCredits = await addUserCredits(userId, creditsToAdd);
      message = `Successfully added ${creditsToAdd} credits to user ${userId}. Current credits: ${finalCredits}`;
    }

    return secureJson({
      success: true,
      message,
      receivedEvent: type
    });
  } catch (error) {
    console.error('[Billing Webhook] Error processing webhook:', error);
    return secureJson(
      { error: 'Failed to process billing webhook' },
      { status: 500 }
    );
  }
}
