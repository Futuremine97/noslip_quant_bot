"use client";

import { useCallback, useEffect, useState } from "react";

import {
  WALLET_EVENT,
  WalletConnectButton,
  type WalletSessionSummary,
} from "@/app/components/WalletConnectButton";

type CreditAccountResponse = {
  ok: boolean;
  account?: {
    userId: string;
    walletAddress?: string;
    balance: number;
  };
  error?: string;
};

type PaymentIntent = {
  id: string;
  chain: "base" | "base-sepolia";
  asset: "USDC";
  amountUsd: number;
  creditAmount: number;
  status: "pending" | "confirmed" | "failed";
};

type Web3StatusCardProps = {
  compact?: boolean;
};

export function Web3StatusCard({ compact = false }: Web3StatusCardProps) {
  const [session, setSession] = useState<WalletSessionSummary | null>(null);
  const [balance, setBalance] = useState(0);
  const [intent, setIntent] = useState<PaymentIntent | null>(null);
  const [paymentMode, setPaymentMode] = useState<"mock" | "production" | null>(
    null
  );
  const [isLocalDevelopment, setIsLocalDevelopment] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    const [sessionResponse, balanceResponse] = await Promise.all([
      fetch("/api/web3/session", { cache: "no-store" }),
      fetch("/api/credits/balance", { cache: "no-store" }),
    ]);
    const sessionPayload = (await sessionResponse.json()) as {
      session?: WalletSessionSummary | null;
    };
    const balancePayload =
      (await balanceResponse.json()) as CreditAccountResponse;
    setSession(sessionPayload.session || null);
    if (balancePayload.account) {
      setBalance(balancePayload.account.balance);
    }
  }, []);

  useEffect(() => {
    setIsLocalDevelopment(
      window.location.hostname === "localhost" ||
        window.location.hostname === "127.0.0.1"
    );
    void refresh();
    const handleSessionChange = () => void refresh();
    window.addEventListener(WALLET_EVENT, handleSessionChange);
    return () => window.removeEventListener(WALLET_EVENT, handleSessionChange);
  }, [refresh]);

  const grantDevelopmentCredits = async () => {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/credits/grant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: 100 }),
      });
      const result = (await response.json()) as {
        ok: boolean;
        balance?: number;
        error?: string;
      };
      if (!response.ok) {
        throw new Error(result.error || "Development grant failed");
      }
      setBalance(result.balance || 0);
      setMessage("Granted 100 local development credits.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Grant failed");
    } finally {
      setBusy(false);
    }
  };

  const createPaymentIntent = async () => {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/payments/create-intent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ packageId: "starter" }),
      });
      const result = (await response.json()) as {
        ok: boolean;
        paymentMode?: "mock" | "production";
        intent?: PaymentIntent;
        error?: string;
      };
      if (!response.ok || !result.intent) {
        throw new Error(result.error || "Payment intent creation failed");
      }
      setIntent(result.intent);
      setPaymentMode(result.paymentMode || null);
      setMessage(
        result.paymentMode === "mock"
          ? "Mock Base Sepolia USDC intent created."
          : "USDC payment intent created. Complete payment from your wallet."
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Payment intent failed"
      );
    } finally {
      setBusy(false);
    }
  };

  const confirmMockPayment = async () => {
    if (!intent) {
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/payments/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intentId: intent.id }),
      });
      const result = (await response.json()) as {
        ok: boolean;
        intent?: PaymentIntent;
        balance?: number;
        error?: string;
      };
      if (!response.ok || !result.intent) {
        throw new Error(result.error || "Mock payment confirmation failed");
      }
      setIntent(result.intent);
      setBalance(result.balance || 0);
      setMessage("Mock payment confirmed and credits recorded.");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Payment confirmation failed"
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={`web3-status-card ${compact ? "compact" : ""}`}>
      <div className="web3-status-heading">
        <div>
          <p className="web3-kicker">Optional Base access</p>
          <h2>NoSlip Credits</h2>
        </div>
        <div className="web3-credit-balance">
          <strong>{balance}</strong>
          <span>credits</span>
        </div>
      </div>

      <WalletConnectButton
        session={session}
        onSessionChange={setSession}
      />

      {!compact ? (
        <>
          <div className="web3-feature-grid">
            <span>Personal forecast: 3</span>
            <span>Whale report: 2</span>
            <span>Signal feed: 1</span>
            <span>Tournament: 5</span>
          </div>
          <div className="web3-payment-actions">
            {isLocalDevelopment ? (
              <button
                className="web3-secondary-button"
                type="button"
                disabled={busy}
                onClick={grantDevelopmentCredits}
              >
                Grant 100 dev credits
              </button>
            ) : null}
            <button
              className="web3-secondary-button"
              type="button"
              disabled={busy}
              onClick={createPaymentIntent}
            >
              Create $5 credit intent
            </button>
            {intent?.status === "pending" && paymentMode === "mock" ? (
              <button
                className="web3-primary-button"
                type="button"
                disabled={busy}
                onClick={confirmMockPayment}
              >
                Confirm mock payment
              </button>
            ) : null}
          </div>
          {intent ? (
            <p className="web3-intent-line">
              {intent.chain} / {intent.asset} ${intent.amountUsd} /{" "}
              {intent.creditAmount} credits / {intent.status}
            </p>
          ) : null}
        </>
      ) : null}

      {message ? <p className="web3-status-message">{message}</p> : null}
      <p className="web3-disclaimer">
        Wallet login is optional. Not financial or investment advice. No
        guaranteed trading performance.
      </p>
    </section>
  );
}
