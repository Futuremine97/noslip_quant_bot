import { describe, it, expect } from 'vitest';
import { searchTokens } from './tokens';

describe('searchTokens (LIVE API)', () => {
    it('should fetch real data from Jupiter API and print it to the CLI', async () => {
        console.log("Fetching live data for 'USDC'...");
        
        // Make the actual network request
        const result = await searchTokens('USDC');
        
        // Print the results to the CLI nicely formatted
        console.log("\n--- LIVE JUPITER API RESULTS ---");
        console.log(JSON.stringify(result, null, 2));
        console.log("------------------------------------------\n");
        console.log(`Total results returned: ${result.length}`);

        // Basic assertions to ensure the live API returned the expected structure
        expect(Array.isArray(result)).toBe(true);
        expect(result.length).toBeGreaterThan(0);
        expect(result.length).toBeLessThanOrEqual(5);
        
        // Check if the first result has the expected mapped properties
        expect(result[0]).toHaveProperty('id');
        expect(result[0]).toHaveProperty('symbol');
        expect(result[0]).toHaveProperty('icon');
        expect(result[0]).toHaveProperty('decimals');

        // Check if extra properties from Jupiter were stripped
        expect(result[0]).not.toHaveProperty('name');
        expect(result[0]).not.toHaveProperty('priceBlockId');
        expect(result[0]).not.toHaveProperty('stats1h');
        expect(result[0]).not.toHaveProperty('usdPrice');
    });
});
