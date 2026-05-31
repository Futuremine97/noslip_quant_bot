import { callGemini } from "./agents";

export const runDebate = async (buy: string, wait: string) => {
  const prompt = `
You are a decision system combining two agents.

Input:

[Buy Agent]
${buy}

[Wait Agent]
${wait}

Tasks:

1. Summarize key differences
2. Create a short debate:
   - Buy Agent attacks Wait Agent
   - Wait Agent attacks Buy Agent

3. Decision rule:
- If expected gain > 3% → WAIT
- Else → BUY

Output format:

[Debate]
Buy Agent:
- ...

Wait Agent:
- ...

[Final Decision]
BUY or WAIT

[Confidence]
(0 to 1)
`;

  return await callGemini(prompt);
};