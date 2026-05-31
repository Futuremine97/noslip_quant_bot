'use server';

export async function stepFinder(
  token1: string,
  token2: string,
  amount: number
) {
  const url = `https://api.jup.ag/swap/v1/quote?inputMint=${token1}&outputMint=${token2}&amount=${amount}&slippageBps=50`;

  console.log("Request URL:", url);

  const response = await fetch(url);

  if (!response.ok) {
    const text = await response.text();
    console.error("Jupiter error:", text);

    
    return [];
  }

  const data = await response.json();

  return data.routePlan || [];
}