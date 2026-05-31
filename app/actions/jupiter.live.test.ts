import { describe, it, expect } from 'vitest';
import { getJupiterQuote, extractRouteHops } from './jupiter';

describe('Jupiter API (LIVE)', () => {
    it('should fetch a quote for SOL to USDC and extract route hops', async () => {
        const SOL_MINT = 'So11111111111111111111111111111111111111112';
        const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
        const AMOUNT = '1000000000'; // 1 SOL

        console.log(`Fetching live quote for 1 SOL -> USDC...`);
        const quote = await getJupiterQuote(SOL_MINT, USDC_MINT, AMOUNT);

        expect(quote).not.toBeNull();
        if (quote) {
            console.log("\n--- LIVE JUPITER QUOTE ---");
            console.log(`Input Amount: ${quote.inAmount}`);
            console.log(`Output Amount: ${quote.outAmount}`);
            console.log(`Price Impact: ${quote.priceImpactPct}%`);
            
            const hops = await extractRouteHops(quote);
            console.log("\n--- EXTRACTED ROUTE HOPS ---");
            console.log(JSON.stringify(hops, null, 2));
            console.log("----------------------------\n");

            expect(Array.isArray(hops)).toBe(true);
            expect(hops.length).toBeGreaterThan(0);
            expect(hops[0]).toHaveProperty('dex');
            expect(hops[0]).toHaveProperty('inputMint');
            expect(hops[0]).toHaveProperty('outputMint');
        }
    });

    it('should return null for invalid mints', async () => {
        const result = await getJupiterQuote('invalid', 'invalid', '1000');
        expect(result).toBeNull();
    });
});
