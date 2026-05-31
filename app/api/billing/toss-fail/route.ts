import { NextResponse } from 'next/server';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const code = searchParams.get('code') || 'UNKNOWN_ERROR';
    const message = searchParams.get('message') || 'Payment failed';
    const orderId = searchParams.get('orderId');

    console.warn('[Toss Fail Callback] Payment failed:', { code, message, orderId });

    return NextResponse.redirect(
      new URL(`/?payment=fail&code=${code}&message=${encodeURIComponent(message)}`, request.url)
    );
  } catch (error: any) {
    console.error('[Toss Fail Callback] Unexpected exception:', error);
    return NextResponse.redirect(
      new URL('/?payment=fail&code=SERVER_ERROR&message=Failed+to+process+payment+failure', request.url)
    );
  }
}
