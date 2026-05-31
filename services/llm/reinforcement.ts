import fs from "node:fs/promises";
import path from "node:path";

type InvestorLensRecord = {
  lens: string;
  weight: number;
  avgReward: number;
  rewardCount: number;
  lastReward: number | null;
  lastUpdatedDate: string | null;
  updatedAt: string | null;
};

export type MacbookAgentSnapshot = {
  generatedAt: string | null;
  name: string;
  weight: number;
  avgReward: number;
  rewardCount: number;
  hitCount: number;
  hitRate: number;
  lastReward: number | null;
  lastReferenceDate: string | null;
  lastRealizedDate: string | null;
  lastRealizedReturnPct: number | null;
  lastCoverageRatio: number | null;
  championAvgReward: number | null;
  championAlignmentScore: number | null;
  championRewardCount: number | null;
  championPreferredCps: number | null;
  updatedAt: string | null;
};

export type SpikeSustainModelSnapshot = {
  model: string;
  weight: number;
  avgReward: number;
  rewardCount: number;
  hitCount: number;
  hitRate: number;
  lastReward: number | null;
  lastReferenceDate: string | null;
  lastRealizedDate: string | null;
  lastRealizedSpikeSustainSeconds: number | null;
  lastRealizedMaxSpikePct: number | null;
  updatedAt: string | null;
};

export type SpikeSustainAgentSnapshot = {
  generatedAt: string | null;
  leader: string;
  models: SpikeSustainModelSnapshot[];
};

export type InvestorLensSnapshot = {
  generatedAt: string | null;
  leader: string;
  lenses: InvestorLensRecord[];
  macbookAgent: MacbookAgentSnapshot;
  spikeSustainAgent: SpikeSustainAgentSnapshot;
};

const DEFAULT_LENSES = ["buffett", "druckenmiller", "lynch", "dalio"] as const;
const DEFAULT_MACBOOK_AGENT: MacbookAgentSnapshot = {
  generatedAt: null,
  name: "Macbook",
  weight: 1,
  avgReward: 0,
  rewardCount: 0,
  hitCount: 0,
  hitRate: 0,
  lastReward: null,
  lastReferenceDate: null,
  lastRealizedDate: null,
  lastRealizedReturnPct: null,
  lastCoverageRatio: null,
  championAvgReward: null,
  championAlignmentScore: null,
  championRewardCount: null,
  championPreferredCps: null,
  updatedAt: null,
};
const DEFAULT_SPIKE_SUSTAIN_AGENT: SpikeSustainAgentSnapshot = {
  generatedAt: null,
  leader: "prophet",
  models: [
    {
      model: "prophet",
      weight: 1,
      avgReward: 0,
      rewardCount: 0,
      hitCount: 0,
      hitRate: 0,
      lastReward: null,
      lastReferenceDate: null,
      lastRealizedDate: null,
      lastRealizedSpikeSustainSeconds: null,
      lastRealizedMaxSpikePct: null,
      updatedAt: null,
    },
    {
      model: "timesfm",
      weight: 1,
      avgReward: 0,
      rewardCount: 0,
      hitCount: 0,
      hitRate: 0,
      lastReward: null,
      lastReferenceDate: null,
      lastRealizedDate: null,
      lastRealizedSpikeSustainSeconds: null,
      lastRealizedMaxSpikePct: null,
      updatedAt: null,
    },
  ],
};
const DEFAULT_SNAPSHOT: InvestorLensSnapshot = {
  generatedAt: null,
  leader: "buffett",
  lenses: DEFAULT_LENSES.map((lens) => ({
    lens,
    weight: 1,
    avgReward: 0,
    rewardCount: 0,
    lastReward: null,
    lastUpdatedDate: null,
    updatedAt: null,
  })),
  macbookAgent: DEFAULT_MACBOOK_AGENT,
  spikeSustainAgent: DEFAULT_SPIKE_SUSTAIN_AGENT,
};

const LOCAL_SNAPSHOT_PATH = path.join(
  process.cwd(),
  "services",
  "trader",
  "model_cache",
  "investor_lens_state.json"
);
const LOCAL_MACBOOK_AGENT_PATH = path.join(
  process.cwd(),
  "services",
  "trader",
  "model_cache",
  "macbook_agent_state.json"
);
const LOCAL_SPIKE_SUSTAIN_AGENT_PATH = path.join(
  process.cwd(),
  "services",
  "trader",
  "model_cache",
  "spike_sustain_state.json"
);

const normalizeMacbookAgent = (value: any): MacbookAgentSnapshot => ({
  generatedAt: value?.generatedAt ? String(value.generatedAt) : null,
  name: value?.name ? String(value.name) : DEFAULT_MACBOOK_AGENT.name,
  weight: Number.isFinite(Number(value?.weight)) ? Number(value.weight) : 1,
  avgReward: Number.isFinite(Number(value?.avgReward)) ? Number(value.avgReward) : 0,
  rewardCount: Number.isFinite(Number(value?.rewardCount)) ? Number(value.rewardCount) : 0,
  hitCount: Number.isFinite(Number(value?.hitCount)) ? Number(value.hitCount) : 0,
  hitRate: Number.isFinite(Number(value?.hitRate)) ? Number(value.hitRate) : 0,
  lastReward:
    value?.lastReward != null && Number.isFinite(Number(value.lastReward))
      ? Number(value.lastReward)
      : null,
  lastReferenceDate: value?.lastReferenceDate ? String(value.lastReferenceDate) : null,
  lastRealizedDate: value?.lastRealizedDate ? String(value.lastRealizedDate) : null,
  lastRealizedReturnPct:
    value?.lastRealizedReturnPct != null &&
    Number.isFinite(Number(value.lastRealizedReturnPct))
      ? Number(value.lastRealizedReturnPct)
      : null,
  lastCoverageRatio:
    value?.lastCoverageRatio != null && Number.isFinite(Number(value.lastCoverageRatio))
      ? Number(value.lastCoverageRatio)
      : null,
  championAvgReward:
    value?.championAvgReward != null && Number.isFinite(Number(value.championAvgReward))
      ? Number(value.championAvgReward)
      : null,
  championAlignmentScore:
    value?.championAlignmentScore != null &&
    Number.isFinite(Number(value.championAlignmentScore))
      ? Number(value.championAlignmentScore)
      : null,
  championRewardCount:
    value?.championRewardCount != null &&
    Number.isFinite(Number(value.championRewardCount))
      ? Number(value.championRewardCount)
      : null,
  championPreferredCps:
    value?.championPreferredCps != null &&
    Number.isFinite(Number(value.championPreferredCps))
      ? Number(value.championPreferredCps)
      : null,
  updatedAt: value?.updatedAt ? String(value.updatedAt) : null,
});

const normalizeSpikeSustainAgent = (value: any): SpikeSustainAgentSnapshot => ({
  generatedAt: value?.generatedAt ? String(value.generatedAt) : null,
  leader: value?.leader ? String(value.leader).toLowerCase() : DEFAULT_SPIKE_SUSTAIN_AGENT.leader,
  models: Array.isArray(value?.models) && value.models.length > 0
    ? value.models
        .map((item: any) => ({
          model: String(item?.model || "").toLowerCase(),
          weight: Number.isFinite(Number(item?.weight)) ? Number(item.weight) : 1,
          avgReward: Number.isFinite(Number(item?.avgReward)) ? Number(item.avgReward) : 0,
          rewardCount: Number.isFinite(Number(item?.rewardCount)) ? Number(item.rewardCount) : 0,
          hitCount: Number.isFinite(Number(item?.hitCount)) ? Number(item.hitCount) : 0,
          hitRate: Number.isFinite(Number(item?.hitRate)) ? Number(item.hitRate) : 0,
          lastReward:
            item?.lastReward != null && Number.isFinite(Number(item.lastReward))
              ? Number(item.lastReward)
              : null,
          lastReferenceDate: item?.lastReferenceDate ? String(item.lastReferenceDate) : null,
          lastRealizedDate: item?.lastRealizedDate ? String(item.lastRealizedDate) : null,
          lastRealizedSpikeSustainSeconds:
            item?.lastRealizedSpikeSustainSeconds != null &&
            Number.isFinite(Number(item.lastRealizedSpikeSustainSeconds))
              ? Number(item.lastRealizedSpikeSustainSeconds)
              : null,
          lastRealizedMaxSpikePct:
            item?.lastRealizedMaxSpikePct != null &&
            Number.isFinite(Number(item.lastRealizedMaxSpikePct))
              ? Number(item.lastRealizedMaxSpikePct)
              : null,
          updatedAt: item?.updatedAt ? String(item.updatedAt) : null,
        }))
        .filter((item: SpikeSustainModelSnapshot) => item.model)
    : DEFAULT_SPIKE_SUSTAIN_AGENT.models,
});

const normalizeSnapshot = (
  value: any,
  macbookValue?: any,
  spikeSustainValue?: any
): InvestorLensSnapshot => {
  const rawLenses = Array.isArray(value?.lenses) ? value.lenses : DEFAULT_SNAPSHOT.lenses;
  const lenses = rawLenses
    .map((item: any) => ({
      lens: String(item?.lens || "").toLowerCase(),
      weight: Number.isFinite(Number(item?.weight)) ? Number(item.weight) : 1,
      avgReward: Number.isFinite(Number(item?.avgReward)) ? Number(item.avgReward) : 0,
      rewardCount: Number.isFinite(Number(item?.rewardCount)) ? Number(item.rewardCount) : 0,
      lastReward:
        item?.lastReward != null && Number.isFinite(Number(item.lastReward))
          ? Number(item.lastReward)
          : null,
      lastUpdatedDate: item?.lastUpdatedDate ? String(item.lastUpdatedDate) : null,
      updatedAt: item?.updatedAt ? String(item.updatedAt) : null,
    }))
    .filter((item: InvestorLensRecord) => item.lens);

  return {
    generatedAt: value?.generatedAt ? String(value.generatedAt) : null,
    leader: value?.leader ? String(value.leader).toLowerCase() : lenses[0]?.lens || "buffett",
    lenses: lenses.length > 0 ? lenses : DEFAULT_SNAPSHOT.lenses,
    macbookAgent: normalizeMacbookAgent(macbookValue ?? value?.macbookAgent),
    spikeSustainAgent: normalizeSpikeSustainAgent(
      spikeSustainValue ?? value?.spikeSustainAgent
    ),
  };
};

const fetchRemoteSnapshot = async (): Promise<InvestorLensSnapshot | null> => {
  const apiBase = (process.env.PREDICTION_API_URL || "").trim();
  if (!apiBase) {
    return null;
  }

  const headers: Record<string, string> = {};
  if (process.env.PREDICTION_API_TOKEN) {
    headers.Authorization = `Bearer ${process.env.PREDICTION_API_TOKEN}`;
  }

  try {
    const response = await fetch(`${apiBase.replace(/\/$/, "")}/reinforcement-state`, {
      headers,
      signal: AbortSignal.timeout(5000),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    return normalizeSnapshot(
      data?.investorLens,
      data?.macbookAgent,
      data?.spikeSustainAgent
    );
  } catch {
    return null;
  }
};

const readLocalSnapshot = async (): Promise<InvestorLensSnapshot> => {
  try {
    const [lensText, macbookText, spikeSustainText] = await Promise.all([
      fs.readFile(LOCAL_SNAPSHOT_PATH, "utf-8"),
      fs.readFile(LOCAL_MACBOOK_AGENT_PATH, "utf-8").catch(() => ""),
      fs.readFile(LOCAL_SPIKE_SUSTAIN_AGENT_PATH, "utf-8").catch(() => ""),
    ]);
    const lensValue = JSON.parse(lensText);
    const macbookValue = macbookText ? JSON.parse(macbookText) : null;
    const spikeSustainValue = spikeSustainText ? JSON.parse(spikeSustainText) : null;
    return normalizeSnapshot(lensValue, macbookValue, spikeSustainValue);
  } catch {
    return DEFAULT_SNAPSHOT;
  }
};

export const getInvestorLensSnapshot = async (): Promise<InvestorLensSnapshot> => {
  const remote = await fetchRemoteSnapshot();
  if (remote) {
    return remote;
  }
  return readLocalSnapshot();
};
