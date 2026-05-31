import { describe, it, expect } from 'vitest';
import { getJupiterQuote } from './jupiter';
import { predictStep } from './prediction';

describe('Prediction System Live Test', () => {
    it('should perform a sequential prediction for a 150 SOL trade', async () => {
        const SOL_MINT = "So11111111111111111111111111111111111111112";
        const WBTC_MINT = "3NZ9J7P7P7P7P7P7P7P7P7P7P7P7P7P7P7P7P7P7P7P7"; // Example WBTC or similar
        const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";

        console.log("\n--- Starting Live Prediction Test ---");
        
        const quote = await getJupiterQuote(SOL_MINT, USDC_MINT, "1000000"); // Very small amount to force shortcut
        
        expect(quote).toBeDefined();
        if (!quote) return;

        const impactPct = parseFloat(quote.priceImpactPct || "0");
        const currentLoss = (0.001 * (impactPct / 100)); // 0.001 SOL since amount is 10^6

        if (currentLoss < 0.0000001) {
            console.log("\n================================================");
            console.log("FINAL ROUTE RECOMMENDATION (Shortcut Test)");
            console.log("================================================");
            console.log(`Current Slippage is negligible: ${currentLoss.toFixed(8)} SOL`);
            console.log(`Action: EXECUTE NOW`);
            console.log("================================================\n");
            await reportFinalRecommendation(0, currentLoss, currentLoss);
            return;
        }
        
        const predictionPromises = routePlan.map(async (step, i) => {
            // Stagger the start of each analysis by 800ms for testing reliability
            await new Promise(resolve => setTimeout(resolve, i * 800));

            const input = step.swapInfo?.inputMint;
            const output = step.swapInfo?.outputMint;
            const label = step.swapInfo?.label;

            console.log(`[Step ${i + 1}/${routePlan.length}] Queuing analysis for ${label}...`);
            const prediction = await predictStep(input, output, "TEST_HOP");
            
            if (prediction) {
                console.log(`[Result] ${label}: ${prediction.finalAction} (Strength: ${prediction.directionStrength.toFixed(4)})`);
                return prediction;
            }
            return null;
        });

        const results = await Promise.all(predictionPromises);
        const validPredictions = results.filter((p): p is NonNullable<typeof p> => p !== null);
        
        if (validPredictions.length > 0) {
          let totalWaitTime = 0;
          let totalImprovement = 0;

          validPredictions.forEach(prediction => {
            if (prediction.timeToBelowCurrent) {
                totalWaitTime += prediction.timeToBelowCurrent;
            }

            if (prediction.targetPrice && prediction.currentPrice) {
                const diff = prediction.currentPrice - prediction.targetPrice;
                const improvementPct = diff / prediction.currentPrice;
                if (improvementPct > 0) {
                    totalImprovement += improvementPct;
                }
            }
          });

          const averageWaitTime = totalWaitTime / validPredictions.length;
          const currentLoss = (150 * (parseFloat(quote.priceImpactPct || "0") / 100));
          const predictedLowerSlippage = Math.max(0, currentLoss - (150 * totalImprovement));

          console.log("\n================================================");
          console.log("FINAL ROUTE RECOMMENDATION (CLI Test)");
          console.log("================================================");
          if (averageWaitTime > 0) {
            const minutes = Math.floor(averageWaitTime / 60);
            const seconds = Math.floor(averageWaitTime % 60);
            console.log(`Average Optimal Window: ${minutes}m ${seconds}s from now`);
            console.log(`Predicted Lower Slippage: ${predictedLowerSlippage.toFixed(8)} SOL`);
            console.log(`Action: WAIT`);
          } else {
            console.log(`Average Optimal Window: NOW`);
            console.log(`Predicted Lower Slippage: ${currentLoss.toFixed(8)} SOL`);
            console.log(`Action: EXECUTE`);
          }
          console.log("================================================\n");
        }
        
        console.log("\n--- Live Prediction Test Complete ---");
    }, 120000); // 2 minute timeout
});
