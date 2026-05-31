import { NextResponse } from 'next/server';
import { addUserCredits, updateUserPlan } from '@/app/actions/mockDb';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const paymentKey = searchParams.get('paymentKey');
    const orderId = searchParams.get('orderId');
    const amount = searchParams.get('amount');

    console.log('[Toss Success Callback] Query parameters:', { paymentKey, orderId, amount });

    if (!paymentKey || !orderId || !amount) {
      return NextResponse.redirect(
        new URL('/?payment=fail&code=INVALID_PARAMS&message=Missing+required+payment+parameters', request.url)
      );
    }

    // 1. Confirm the payment with Toss Payments API
    const secretKey = process.env.TOSS_SECRET_KEY || '';
    const basicAuth = Buffer.from(`${secretKey}:`).toString('base64');

    const confirmResponse = await fetch('https://api.tosspayments.com/v1/payments/confirm', {
      method: 'POST',
      headers: {
        Authorization: `Basic ${basicAuth}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ paymentKey, orderId, amount: Number(amount) }),
    });

    const responseBody = await confirmResponse.json();

    if (!confirmResponse.ok) {
      console.error('[Toss Success Callback] Confirmation failed:', responseBody);
      const errMsg = responseBody.message || 'Payment confirmation failed';
      return NextResponse.redirect(
        new URL(`/?payment=fail&code=CONFIRM_FAILED&message=${encodeURIComponent(errMsg)}`, request.url)
      );
    }

    console.log('[Toss Success Callback] Confirmation success:', responseBody);

    // 2. Parse the custom orderId to extract purchase details
    // Format: user__[userId]__[type]__[value]__[timestamp]
    const parts = orderId.split('__');
    if (parts.length < 4 || parts[0] !== 'user') {
      console.error('[Toss Success Callback] Invalid orderId format:', orderId);
      return NextResponse.redirect(
        new URL('/?payment=fail&code=INVALID_ORDER_ID&message=Invalid+order+format', request.url)
      );
    }

    const userId = parts[1];
    const purchaseType = parts[2]; // 'credits' or 'plan'
    const purchaseValue = parts[3]; // amount of credits (e.g. '500') or plan name (e.g. 'pro')

    let message = '';
    if (purchaseType === 'plan') {
      const planName = purchaseValue as 'basic' | 'pro' | 'enterprise';
      await updateUserPlan(userId, planName);
      message = `Successfully upgraded to ${planName} plan.`;
    } else if (purchaseType === 'credits') {
      const creditsToAdd = Number(purchaseValue);
      await addUserCredits(userId, creditsToAdd);
      message = `Successfully charged ${creditsToAdd} credits.`;
    }

    console.log('[Toss Success Callback] Applied database changes:', { userId, purchaseType, purchaseValue });

    // 3. Redirect back to homepage with success flag and details
    return NextResponse.redirect(
      new URL(`/?payment=success&type=${purchaseType}&value=${purchaseValue}`, request.url)
    );
  } catch (error: any) {
    console.error('[Toss Success Callback] Unexpected exception:', error);
    return NextResponse.redirect(
      new URL(`/?payment=fail&code=SERVER_ERROR&message=${encodeURIComponent(error.message || 'Internal server error')}`, request.url)
    );
  }
}
