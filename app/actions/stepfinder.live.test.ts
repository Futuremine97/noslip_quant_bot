import { describe, it, expect } from 'vitest';
import { stepFinder } from './stepfinder';

describe('stepFinder (LIVE API)', () => {
    it('should fetch real routing data from Jupiter API for SOL -> ETH (multi-hop) and print it', async () => {
        // SOL mint address
        const startMint = 'So11111111111111111111111111111111111111112';
        // ETH (Portal/Wormhole) mint address
        const endMint = '7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs';
        // 1 SOL in lamports (SOL has 9 decimals)
        const amount = 1000000000;

        console.log("Fetching live routing data for 1 SOL to ETH...");
        
        // Make the actual network request
        const result = await stepFinder(startMint, endMint, amount);
        
        // Print the results to the CLI nicely formatted
        console.log("\n--- LIVE JUPITER API ROUTE RESULTS ---");
        console.log(JSON.stringify(result, null, 2));
        console.log("------------------------------------------\n");
        console.log(`Total routing steps returned: ${result.length}`);

        // Assertions to ensure the live API returned the expected structure and multiple hops
        expect(Array.isArray(result)).toBe(true);
        expect(result.length).toBeGreaterThan(1); // Expecting multi-hop route for SOL -> ETH
        
        // Check if the first step has the expected nested swapInfo structure
        expect(result[0]).toHaveProperty('percent');
        expect(result[0]).toHaveProperty('swapInfo');
        
        const firstSwap = result[0].swapInfo;
        expect(firstSwap).toHaveProperty('ammKey');
        expect(firstSwap).toHaveProperty('label');
        expect(firstSwap).toHaveProperty('inputMint');
        expect(firstSwap).toHaveProperty('outputMint');
        expect(firstSwap).toHaveProperty('inAmount');
        expect(firstSwap).toHaveProperty('outAmount');
    });
});
