import type { BaseChainConfig } from "@/lib/web3/base";

type ProviderRequest = {
  method: string;
  params?: unknown[] | Record<string, unknown>;
};

export type Eip1193Provider = {
  request<T = unknown>(request: ProviderRequest): Promise<T>;
  on?: (event: string, listener: (...args: unknown[]) => void) => void;
  removeListener?: (
    event: string,
    listener: (...args: unknown[]) => void
  ) => void;
};

declare global {
  interface Window {
    ethereum?: Eip1193Provider;
  }
}

export function getInjectedProvider() {
  return typeof window === "undefined" ? undefined : window.ethereum;
}

export async function getConnectedAccounts(provider: Eip1193Provider) {
  return provider.request<string[]>({ method: "eth_accounts" });
}

export async function requestWalletAccount(provider: Eip1193Provider) {
  const accounts = await provider.request<string[]>({
    method: "eth_requestAccounts",
  });
  const address = accounts[0];
  if (!address) {
    throw new Error("The wallet did not return an account");
  }
  return address;
}

export async function ensureWalletChain(
  provider: Eip1193Provider,
  chain: BaseChainConfig
) {
  const currentChainId = await provider.request<string>({
    method: "eth_chainId",
  });
  if (Number.parseInt(currentChainId, 16) === chain.chainId) {
    return;
  }

  try {
    await provider.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: chain.chainIdHex }],
    });
  } catch (error) {
    const code = Number((error as { code?: number }).code);
    if (code !== 4902) {
      throw error;
    }
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: chain.chainIdHex,
          chainName: chain.displayName,
          nativeCurrency: chain.nativeCurrency,
          rpcUrls: chain.rpcUrls,
          blockExplorerUrls: [chain.blockExplorerUrl],
        },
      ],
    });
  }
}

export async function signWalletMessage(
  provider: Eip1193Provider,
  address: string,
  message: string
) {
  const encodedMessage = `0x${Array.from(new TextEncoder().encode(message))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")}`;
  return provider.request<string>({
    method: "personal_sign",
    params: [encodedMessage, address],
  });
}

export function shortenWalletAddress(address: string) {
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}
