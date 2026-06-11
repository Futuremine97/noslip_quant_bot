"use client";

import { useEffect, useState } from "react";

import type { BaseChainConfig } from "@/lib/web3/base";
import {
  ensureWalletChain,
  getInjectedProvider,
  requestWalletAccount,
  shortenWalletAddress,
  signWalletMessage,
} from "@/lib/web3/wallet";

export type WalletSessionSummary = {
  userId: string;
  walletAddress: string;
  chainId: number;
  expiresAt: number;
  mode: "wallet" | "mock";
};

type SessionResponse = {
  ok: boolean;
  authenticated: boolean;
  session?: WalletSessionSummary | null;
  error?: string;
};

type WalletConnectButtonProps = {
  session: WalletSessionSummary | null;
  onSessionChange: (session: WalletSessionSummary | null) => void;
};

const WALLET_STORAGE_KEY = "noslip.wallet.address";
const WALLET_EVENT = "noslip-wallet-session-changed";

function notifySessionChange() {
  window.dispatchEvent(new Event(WALLET_EVENT));
}

export function WalletConnectButton({
  session,
  onSessionChange,
}: WalletConnectButtonProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [isLocalHost, setIsLocalHost] = useState(false);

  useEffect(() => {
    setIsLocalHost(
      ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname)
    );
  }, []);

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      const provider = getInjectedProvider();
      if (!provider) {
        throw new Error("No injected EVM wallet was found");
      }
      const address = await requestWalletAccount(provider);
      const challengeResponse = await fetch("/api/web3/challenge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address }),
      });
      const challenge = (await challengeResponse.json()) as {
        ok: boolean;
        message?: string;
        chain?: BaseChainConfig;
        error?: string;
      };
      if (
        !challengeResponse.ok ||
        !challenge.message ||
        !challenge.chain
      ) {
        throw new Error(challenge.error || "Unable to create wallet challenge");
      }

      await ensureWalletChain(provider, challenge.chain);
      const signature = await signWalletMessage(
        provider,
        address,
        challenge.message
      );
      const sessionResponse = await fetch("/api/web3/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address,
          message: challenge.message,
          signature,
        }),
      });
      const result = (await sessionResponse.json()) as SessionResponse;
      if (!sessionResponse.ok || !result.session) {
        throw new Error(result.error || "Wallet authentication failed");
      }

      localStorage.setItem(WALLET_STORAGE_KEY, result.session.walletAddress);
      onSessionChange(result.session);
      notifySessionChange();
    } catch (connectError) {
      setError(
        connectError instanceof Error
          ? connectError.message
          : "Wallet connection failed"
      );
    } finally {
      setBusy(false);
    }
  };

  const connectMock = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/web3/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mock: true }),
      });
      const result = (await response.json()) as SessionResponse;
      if (!response.ok || !result.session) {
        throw new Error(result.error || "Mock wallet session failed");
      }
      localStorage.setItem(WALLET_STORAGE_KEY, result.session.walletAddress);
      onSessionChange(result.session);
      notifySessionChange();
    } catch (mockError) {
      setError(
        mockError instanceof Error
          ? mockError.message
          : "Mock wallet session failed"
      );
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError("");
    try {
      await fetch("/api/web3/session", { method: "DELETE" });
      localStorage.removeItem(WALLET_STORAGE_KEY);
      onSessionChange(null);
      notifySessionChange();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="web3-wallet-actions">
      {session ? (
        <>
          <span className="web3-wallet-address">
            {shortenWalletAddress(session.walletAddress)}
            {session.mode === "mock" ? " (demo)" : ""}
          </span>
          <button
            className="web3-secondary-button"
            type="button"
            disabled={busy}
            onClick={disconnect}
          >
            Disconnect
          </button>
        </>
      ) : (
        <>
          <button
            className="web3-primary-button"
            type="button"
            disabled={busy}
            onClick={connect}
          >
            {busy ? "Connecting..." : "Connect Base wallet"}
          </button>
          {isLocalHost ? (
            <button
              className="web3-secondary-button"
              type="button"
              disabled={busy}
              onClick={connectMock}
            >
              Use demo wallet
            </button>
          ) : null}
        </>
      )}
      {error ? <span className="web3-inline-error">{error}</span> : null}
    </div>
  );
}

export { WALLET_EVENT };
