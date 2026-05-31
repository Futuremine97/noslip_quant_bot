type FredPoint = {
  date: string;
  value: number;
};

export type MacroBackdrop = {
  source: string;
  m2LatestDate: string | null;
  m2LevelBillions: number | null;
  m2ThreeMonthPct: number | null;
  m2YearPct: number | null;
  policyRateLatestDate: string | null;
  policyRatePct: number | null;
  policyRateThreeMonthChangeBps: number | null;
  policyRateYearChangeBps: number | null;
  liquidityRegime: string;
  rateRegime: string;
  summary: string;
};

const FRED_CSV_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=";
const MACRO_CACHE_TTL_MS = 6 * 60 * 60 * 1000;

let macroCache:
  | {
      expiresAt: number;
      value: MacroBackdrop;
    }
  | null = null;

const safeNumber = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const parseFredCsv = (csvText: string): FredPoint[] => {
  return csvText
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .map((line) => line.split(","))
    .map(([date, value]) => ({
      date: (date || "").trim(),
      value: Number((value || "").trim()),
    }))
    .filter(
      (point) =>
        point.date &&
        Number.isFinite(point.value) &&
        !Number.isNaN(point.value)
    );
};

const fetchFredSeries = async (seriesId: string): Promise<FredPoint[]> => {
  const response = await fetch(`${FRED_CSV_BASE}${encodeURIComponent(seriesId)}`, {
    next: { revalidate: 60 * 60 * 6 },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch ${seriesId} (${response.status})`);
  }

  return parseFredCsv(await response.text());
};

const findPointOnOrBefore = (
  points: FredPoint[],
  targetDate: string
): FredPoint | null => {
  const target = new Date(targetDate);
  if (Number.isNaN(target.getTime())) {
    return null;
  }

  for (let index = points.length - 1; index >= 0; index -= 1) {
    const pointDate = new Date(points[index].date);
    if (!Number.isNaN(pointDate.getTime()) && pointDate <= target) {
      return points[index];
    }
  }

  return null;
};

const percentChange = (latest: number | null, previous: number | null) => {
  if (
    latest == null ||
    previous == null ||
    !Number.isFinite(latest) ||
    !Number.isFinite(previous) ||
    previous === 0
  ) {
    return null;
  }
  return latest / previous - 1;
};

const basisPointChange = (latest: number | null, previous: number | null) => {
  if (
    latest == null ||
    previous == null ||
    !Number.isFinite(latest) ||
    !Number.isFinite(previous)
  ) {
    return null;
  }
  return (latest - previous) * 100;
};

const classifyLiquidityRegime = (
  threeMonthPct: number | null,
  yearPct: number | null
) => {
  if (threeMonthPct == null && yearPct == null) {
    return "유동성 데이터 확인 필요";
  }

  const threeMonth = threeMonthPct ?? 0;
  const year = yearPct ?? 0;

  if (threeMonth > 0.01 && year > 0.02) {
    return "유동성 확장";
  }
  if (threeMonth < -0.005 && year < 0) {
    return "유동성 수축";
  }
  return "유동성 혼조";
};

const classifyRateRegime = (
  threeMonthChangeBps: number | null,
  yearChangeBps: number | null
) => {
  if (threeMonthChangeBps == null && yearChangeBps == null) {
    return "금리 데이터 확인 필요";
  }

  const shortMove = threeMonthChangeBps ?? 0;
  const longMove = yearChangeBps ?? 0;

  if (shortMove <= -20 || longMove <= -50) {
    return "금리 완화";
  }
  if (shortMove >= 20 || longMove >= 50) {
    return "금리 긴축";
  }
  return "금리 횡보";
};

const formatPct = (value: number | null, digits = 1) =>
  value == null ? "unknown" : `${(value * 100).toFixed(digits)}%`;

const formatBps = (value: number | null, digits = 0) =>
  value == null ? "unknown" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}bp`;

export const getMacroBackdrop = async (): Promise<MacroBackdrop> => {
  if (macroCache && macroCache.expiresAt > Date.now()) {
    return macroCache.value;
  }

  try {
    const [m2Points, ratePoints] = await Promise.all([
      fetchFredSeries("M2SL"),
      fetchFredSeries("DFF"),
    ]);

    const latestM2 = m2Points[m2Points.length - 1] || null;
    const latestRate = ratePoints[ratePoints.length - 1] || null;

    const m2ThreeMonthAgo = latestM2
      ? findPointOnOrBefore(
          m2Points,
          new Date(
            new Date(latestM2.date).setMonth(new Date(latestM2.date).getMonth() - 3)
          )
            .toISOString()
            .slice(0, 10)
        )
      : null;
    const m2YearAgo = latestM2
      ? findPointOnOrBefore(
          m2Points,
          new Date(
            new Date(latestM2.date).setFullYear(new Date(latestM2.date).getFullYear() - 1)
          )
            .toISOString()
            .slice(0, 10)
        )
      : null;

    const rateThreeMonthAgo = latestRate
      ? findPointOnOrBefore(
          ratePoints,
          new Date(
            new Date(latestRate.date).setMonth(new Date(latestRate.date).getMonth() - 3)
          )
            .toISOString()
            .slice(0, 10)
        )
      : null;
    const rateYearAgo = latestRate
      ? findPointOnOrBefore(
          ratePoints,
          new Date(
            new Date(latestRate.date).setFullYear(new Date(latestRate.date).getFullYear() - 1)
          )
            .toISOString()
            .slice(0, 10)
        )
      : null;

    const m2ThreeMonthPct = percentChange(
      latestM2?.value ?? null,
      m2ThreeMonthAgo?.value ?? null
    );
    const m2YearPct = percentChange(
      latestM2?.value ?? null,
      m2YearAgo?.value ?? null
    );
    const rateThreeMonthChangeBps = basisPointChange(
      latestRate?.value ?? null,
      rateThreeMonthAgo?.value ?? null
    );
    const rateYearChangeBps = basisPointChange(
      latestRate?.value ?? null,
      rateYearAgo?.value ?? null
    );

    const liquidityRegime = classifyLiquidityRegime(m2ThreeMonthPct, m2YearPct);
    const rateRegime = classifyRateRegime(
      rateThreeMonthChangeBps,
      rateYearChangeBps
    );

    const value: MacroBackdrop = {
      source: "Federal Reserve Bank of St. Louis FRED (M2SL, DFF)",
      m2LatestDate: latestM2?.date ?? null,
      m2LevelBillions: safeNumber(latestM2?.value),
      m2ThreeMonthPct,
      m2YearPct,
      policyRateLatestDate: latestRate?.date ?? null,
      policyRatePct: safeNumber(latestRate?.value),
      policyRateThreeMonthChangeBps: rateThreeMonthChangeBps,
      policyRateYearChangeBps: rateYearChangeBps,
      liquidityRegime,
      rateRegime,
      summary: `M2는 ${formatPct(m2ThreeMonthPct)}(3개월), ${formatPct(
        m2YearPct
      )}(1년) 변동했고 현재 유동성 상태는 ${liquidityRegime}입니다. 기준이 되는 단기 금리는 ${safeNumber(
        latestRate?.value
      )?.toFixed(2) ?? "unknown"}% 수준이며 최근 3개월 ${formatBps(
        rateThreeMonthChangeBps
      )}, 1년 ${formatBps(rateYearChangeBps)} 변화로 ${rateRegime} 국면입니다.`,
    };

    macroCache = {
      value,
      expiresAt: Date.now() + MACRO_CACHE_TTL_MS,
    };
    return value;
  } catch (error) {
    const fallback: MacroBackdrop = {
      source: "Federal Reserve Bank of St. Louis FRED (M2SL, DFF)",
      m2LatestDate: null,
      m2LevelBillions: null,
      m2ThreeMonthPct: null,
      m2YearPct: null,
      policyRateLatestDate: null,
      policyRatePct: null,
      policyRateThreeMonthChangeBps: null,
      policyRateYearChangeBps: null,
      liquidityRegime: "유동성 데이터 확인 필요",
      rateRegime: "금리 데이터 확인 필요",
      summary: `매크로 데이터를 불러오지 못했습니다: ${
        error instanceof Error ? error.message : String(error)
      }`,
    };

    macroCache = {
      value: fallback,
      expiresAt: Date.now() + 5 * 60 * 1000,
    };
    return fallback;
  }
};
