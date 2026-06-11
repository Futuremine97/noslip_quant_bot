export type BaseChainName = "base" | "base-sepolia";

export type BaseChainConfig = {
  name: BaseChainName;
  chainId: number;
  chainIdHex: `0x${string}`;
  displayName: string;
  rpcUrls: string[];
  blockExplorerUrl: string;
  nativeCurrency: {
    name: "Ether";
    symbol: "ETH";
    decimals: 18;
  };
};

const configuredBaseChainId = Number(
  process.env.NEXT_PUBLIC_BASE_CHAIN_ID || 8453
);
const configuredBaseSepoliaChainId = Number(
  process.env.NEXT_PUBLIC_BASE_SEPOLIA_CHAIN_ID || 84532
);

export const BASE_CHAINS: Record<BaseChainName, BaseChainConfig> = {
  base: {
    name: "base",
    chainId: configuredBaseChainId,
    chainIdHex: `0x${configuredBaseChainId.toString(16)}`,
    displayName: "Base",
    rpcUrls: ["https://mainnet.base.org"],
    blockExplorerUrl: "https://basescan.org",
    nativeCurrency: {
      name: "Ether",
      symbol: "ETH",
      decimals: 18,
    },
  },
  "base-sepolia": {
    name: "base-sepolia",
    chainId: configuredBaseSepoliaChainId,
    chainIdHex: `0x${configuredBaseSepoliaChainId.toString(16)}`,
    displayName: "Base Sepolia",
    rpcUrls: ["https://sepolia.base.org"],
    blockExplorerUrl: "https://sepolia.basescan.org",
    nativeCurrency: {
      name: "Ether",
      symbol: "ETH",
      decimals: 18,
    },
  },
};

export function getBaseChain(name: BaseChainName) {
  return BASE_CHAINS[name];
}

export function getBaseChainById(chainId: number) {
  return Object.values(BASE_CHAINS).find((chain) => chain.chainId === chainId);
}
