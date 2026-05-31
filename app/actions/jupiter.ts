// app/actions/jupiter.ts
'use server'

export async function getJupiterQuote(
    inputMint: string,
    outputMint: string,
    amount: string
): Promise<any | null> {
    // Using the working legacy endpoint reachable in this environment
    const url = `https://api.jup.ag/swap/v1/quote?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=50&onlyDirectRoutes=false&swapMode=ExactIn`;
    
    try {
        const response = await fetch(url);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error(`Jupiter API Failure (${response.status}):`, errorText);
            return null;
        }

        const quote = await response.json();
        return quote;
    } catch (error) {
        console.error("Jupiter API Failure:", error);
        return null;
    }
}

export async function extractRouteHops(quote: any) {
    if (!quote || !quote.routePlan) return [];
    
    return quote.routePlan.map((step: any) => ({
        inputMint: step.swapInfo.inputMint,
        outputMint: step.swapInfo.outputMint,
        dex: step.swapInfo.label,
    }));
}

