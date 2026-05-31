'use server'

export interface TokenSearchResult {
    id: string;
    symbol: string;
    icon: string;
    decimals: number;
}

export async function searchTokens(query: string): Promise<TokenSearchResult[]> {
    if (!query || query.length < 2) return [];

    try {
        // Jupiter Tokens V2 Search API
        const response = await fetch(`https://api.jup.ag/tokens/v2/search?query=${encodeURIComponent(query)}`);
        const data = await response.json();

        // Jupiter V2 returns an array directly
        if (!Array.isArray(data)) {
            return [];
        }

        return data.slice(0, 5).map((token: any) => ({
            id: token.id,
            symbol: token.symbol,
            icon: token.icon,
            decimals: token.decimals
        }));
    } catch (error) {
        console.error("Token Search Error:", error);
        return [];
    }
}
