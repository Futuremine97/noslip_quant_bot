import { buildSp500InformationMap, buildSp500Portfolio } from "@/app/actions/prediction";
import { runPortfolioRebalanceChatAgent } from "@/services/llm/agents";
import { getMacroBackdrop } from "@/services/llm/macro";
import { guardApiRequest, secureJson } from "@/app/api/_lib/security";

export const runtime = "nodejs";

const MAX_FILE_BYTES = 256 * 1024;
const MAX_ROWS = 250;
const MAX_COLUMNS = 12;
const MAX_PROMPT_CHARS = 500;
const ALLOWED_MIME_TYPES = new Set(["text/csv", "application/csv", "text/plain", ""]);
const SYMBOL_PATTERN = /^[A-Z][A-Z0-9.-]{0,9}$/;
const FORMULA_PREFIX_PATTERN = /^[=+\-@]/;

type ParsedPortfolioHolding = {
  symbol: string;
  name?: string | null;
  weightPct: number;
  inputWeight?: number | null;
  inputValue?: number | null;
  inputShares?: number | null;
};

type MapPoint = {
  symbol?: string;
  finalAction?: string | null;
  uncertaintyRatio?: number | null;
  maxUpsidePct?: number | null;
  drawdownLingerSeconds?: number | null;
  spikeSustainSeconds?: number | null;
  darkHorseScore?: number | null;
  darkHorseLabel?: string | null;
  trajectory?: {
    persistenceScore?: number | null;
    regimeShiftRisk?: number | null;
  } | null;
  symmetry?: {
    counterpartSymbol?: string | null;
  } | null;
};

type PortfolioHolding = {
  symbol?: string;
  portfolioWeightPct?: number | null;
  weightPct?: number | null;
  finalAction?: string | null;
  uncertaintyRatio?: number | null;
  maxUpsidePct?: number | null;
  drawdownLingerSeconds?: number | null;
  spikeSustainSeconds?: number | null;
  darkHorseScore?: number | null;
  darkHorseLabel?: string | null;
  annualizedVolatilityPct?: number | null;
  trajectory?: {
    persistenceScore?: number | null;
    regimeShiftRisk?: number | null;
  } | null;
  rationale?: string | null;
};

function sanitizeTextInput(raw: string) {
  if (raw.includes("\0")) {
    throw new Error("CSV contains unsupported null bytes.");
  }
  const cleaned = raw
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/[^\S\n\t\x20-\x7E\u00A0-\uD7FF\uE000-\uFFFD]/g, "");
  return cleaned;
}

function parseCsvRows(text: string) {
  const rows: string[][] = [];
  let currentRow: string[] = [];
  let currentCell = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];

    if (inQuotes) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          currentCell += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        currentCell += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
      continue;
    }

    if (char === ",") {
      currentRow.push(currentCell.trim());
      currentCell = "";
      continue;
    }

    if (char === "\n") {
      currentRow.push(currentCell.trim());
      if (currentRow.some((value) => value.length > 0)) {
        rows.push(currentRow);
      }
      currentRow = [];
      currentCell = "";
      continue;
    }

    currentCell += char;
  }

  currentRow.push(currentCell.trim());
  if (currentRow.some((value) => value.length > 0)) {
    rows.push(currentRow);
  }

  if (inQuotes) {
    throw new Error("CSV contains an unclosed quoted field.");
  }

  return rows;
}

function normalizeHeader(header: string) {
  return header
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function findHeaderIndex(headers: string[], aliases: string[]) {
  for (const alias of aliases) {
    const index = headers.indexOf(alias);
    if (index >= 0) {
      return index;
    }
  }
  return -1;
}

function safeNumber(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const normalized = value.replace(/[$,%\s,]/g, "");
  const numeric = Number(normalized);
  return Number.isFinite(numeric) ? numeric : null;
}

function normalizeWeightInput(rawWeight: number | null) {
  if (rawWeight == null || rawWeight <= 0) {
    return null;
  }
  if (rawWeight > 1.5) {
    return rawWeight / 100;
  }
  return rawWeight;
}

function normalizeSymbol(value: string) {
  const trimmed = value.trim().toUpperCase().replace(/\./g, "-");
  if (!trimmed || FORMULA_PREFIX_PATTERN.test(trimmed)) {
    return null;
  }
  if (!SYMBOL_PATTERN.test(trimmed)) {
    return null;
  }
  return trimmed;
}

function parsePortfolioCsvText(text: string) {
  const rows = parseCsvRows(sanitizeTextInput(text));
  if (rows.length < 2) {
    throw new Error("CSV must include a header row and at least one holding.");
  }
  if (rows.length - 1 > MAX_ROWS) {
    throw new Error(`CSV can include up to ${MAX_ROWS} holdings.`);
  }

  const headerRow = rows[0];
  if (headerRow.length > MAX_COLUMNS) {
    throw new Error(`CSV can include up to ${MAX_COLUMNS} columns.`);
  }

  const normalizedHeaders = headerRow.map(normalizeHeader);
  const symbolIndex = findHeaderIndex(normalizedHeaders, [
    "symbol",
    "ticker",
    "asset",
    "stock",
    "security",
  ]);
  if (symbolIndex < 0) {
    throw new Error("CSV needs a symbol or ticker column.");
  }

  const nameIndex = findHeaderIndex(normalizedHeaders, [
    "name",
    "company",
    "company_name",
    "security_name",
  ]);
  const weightIndex = findHeaderIndex(normalizedHeaders, [
    "weight",
    "weight_pct",
    "allocation",
    "allocation_pct",
    "percent",
    "percentage",
  ]);
  const valueIndex = findHeaderIndex(normalizedHeaders, [
    "value",
    "market_value",
    "marketvalue",
    "usd_value",
    "current_value",
    "notional",
  ]);
  const sharesIndex = findHeaderIndex(normalizedHeaders, [
    "shares",
    "qty",
    "quantity",
    "units",
  ]);

  const aggregated = new Map<
    string,
    { symbol: string; name?: string | null; weight?: number; value?: number; shares?: number; rows: number }
  >();
  const unknownSymbols: string[] = [];

  for (let rowIndex = 1; rowIndex < rows.length; rowIndex += 1) {
    const row = rows[rowIndex];
    if (row.length > MAX_COLUMNS) {
      throw new Error(`Row ${rowIndex + 1} exceeds the ${MAX_COLUMNS}-column limit.`);
    }
    const rawSymbol = row[symbolIndex] ?? "";
    const symbol = normalizeSymbol(rawSymbol);
    if (!symbol) {
      if (rawSymbol.trim()) {
        unknownSymbols.push(rawSymbol.trim().slice(0, 32));
      }
      continue;
    }

    const entry = aggregated.get(symbol) ?? {
      symbol,
      name: nameIndex >= 0 ? (row[nameIndex] || "").trim().slice(0, 80) || null : null,
      rows: 0,
    };
    const weight = weightIndex >= 0 ? normalizeWeightInput(safeNumber(row[weightIndex])) : null;
    const value = valueIndex >= 0 ? safeNumber(row[valueIndex]) : null;
    const shares = sharesIndex >= 0 ? safeNumber(row[sharesIndex]) : null;

    entry.weight = (entry.weight ?? 0) + (weight ?? 0);
    entry.value = (entry.value ?? 0) + (value ?? 0);
    entry.shares = (entry.shares ?? 0) + (shares ?? 0);
    entry.rows += 1;
    if (!entry.name && nameIndex >= 0) {
      entry.name = (row[nameIndex] || "").trim().slice(0, 80) || null;
    }
    aggregated.set(symbol, entry);
  }

  const aggregatedRows = Array.from(aggregated.values());
  if (aggregatedRows.length === 0) {
    throw new Error("No valid S&P500-style ticker symbols were found in the CSV.");
  }

  const hasWeights = aggregatedRows.some((entry) => (entry.weight ?? 0) > 0);
  const hasValues = aggregatedRows.some((entry) => (entry.value ?? 0) > 0);
  const hasShares = aggregatedRows.some((entry) => (entry.shares ?? 0) > 0);

  let basis: "weight" | "value" | "shares" | "equal" = "equal";
  if (hasWeights) {
    basis = "weight";
  } else if (hasValues) {
    basis = "value";
  } else if (hasShares) {
    basis = "shares";
  }

  const totals = aggregatedRows.reduce(
    (sum, entry) => {
      if (basis === "weight") {
        return sum + (entry.weight ?? 0);
      }
      if (basis === "value") {
        return sum + (entry.value ?? 0);
      }
      if (basis === "shares") {
        return sum + (entry.shares ?? 0);
      }
      return sum + 1;
    },
    0
  );

  const holdings: ParsedPortfolioHolding[] = aggregatedRows
    .map((entry) => {
      const raw =
        basis === "weight"
          ? entry.weight ?? 0
          : basis === "value"
            ? entry.value ?? 0
            : basis === "shares"
              ? entry.shares ?? 0
              : 1;
      return {
        symbol: entry.symbol,
        name: entry.name,
        weightPct: totals > 0 ? (raw / totals) * 100 : 0,
        inputWeight: entry.weight ?? null,
        inputValue: entry.value ?? null,
        inputShares: entry.shares ?? null,
      };
    })
    .filter((holding) => holding.weightPct > 0)
    .sort((left, right) => right.weightPct - left.weightPct);

  return {
    headers: normalizedHeaders,
    basis,
    holdings,
    unknownSymbols,
    rowsProcessed: aggregatedRows.length,
  };
}

function formatPercent(value: number | null | undefined, digits = 1) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }
  return `${value.toFixed(digits)}%`;
}

function formatDaysFromSeconds(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return "--";
  }
  const days = value / 86_400;
  if (days >= 1) {
    return `${days.toFixed(1)}d`;
  }
  const hours = value / 3_600;
  return `${hours.toFixed(1)}h`;
}

function weightedAverage<T>(
  entries: T[],
  valueGetter: (entry: T) => number | null | undefined,
  weightGetter: (entry: T) => number | null | undefined
) {
  let weighted = 0;
  let total = 0;
  for (const entry of entries) {
    const value = valueGetter(entry);
    const weight = weightGetter(entry);
    if (value == null || weight == null || !Number.isFinite(value) || !Number.isFinite(weight)) {
      continue;
    }
    weighted += value * weight;
    total += weight;
  }
  return total > 0 ? weighted / total : null;
}

function summarizeList(items: Array<{ symbol: string; rationale: string }>) {
  if (!items.length) {
    return "none";
  }
  return items
    .slice(0, 5)
    .map((item) => `${item.symbol} (${item.rationale})`)
    .join(" | ");
}

function buildHoldingInsight(
  holding: ParsedPortfolioHolding,
  mapPoint: MapPoint | undefined,
  optimizedHolding: PortfolioHolding | undefined
) {
  const uncertainty =
    optimizedHolding?.uncertaintyRatio ?? mapPoint?.uncertaintyRatio ?? null;
  const upside = optimizedHolding?.maxUpsidePct ?? mapPoint?.maxUpsidePct ?? null;
  const persistence =
    optimizedHolding?.trajectory?.persistenceScore ??
    mapPoint?.trajectory?.persistenceScore ??
    null;
  const regimeRisk =
    optimizedHolding?.trajectory?.regimeShiftRisk ??
    mapPoint?.trajectory?.regimeShiftRisk ??
    null;
  const finalAction = optimizedHolding?.finalAction ?? mapPoint?.finalAction ?? null;
  const targetWeightPct =
    optimizedHolding?.portfolioWeightPct ?? optimizedHolding?.weightPct ?? 0;
  const darkHorseScore =
    optimizedHolding?.darkHorseScore ?? mapPoint?.darkHorseScore ?? null;

  const score =
    (upside ?? 0) * 4.8 +
    (persistence ?? 0) * 1.6 +
    ((darkHorseScore ?? 0) / 100) * 0.75 -
    (uncertainty ?? 0) * 3.2 -
    (regimeRisk ?? 0) * 2.1 +
    (finalAction === "BUY" ? 0.45 : finalAction === "HOLD" ? 0.08 : -0.7);

  return {
    symbol: holding.symbol,
    currentWeightPct: holding.weightPct,
    targetWeightPct,
    uncertainty,
    upside,
    persistence,
    regimeRisk,
    finalAction,
    darkHorseScore,
    score,
  };
}

function buildReconfigurationAnalysis(
  parsed: ReturnType<typeof parsePortfolioCsvText>,
  informationMap: Awaited<ReturnType<typeof buildSp500InformationMap>>,
  optimizedPortfolio: Awaited<ReturnType<typeof buildSp500Portfolio>>
) {
  const mapPoints = new Map(
    (informationMap.points || []).map((point) => [String(point.symbol || ""), point as MapPoint])
  );
  const optimizedHoldings = new Map(
    (optimizedPortfolio.holdings || []).map((holding) => [
      String(holding.symbol || ""),
      holding as PortfolioHolding,
    ])
  );
  const optimizedSymbols = new Set(optimizedHoldings.keys());
  const darkHorseLookup = new Map(
    ((informationMap.darkHorsePicks as MapPoint[] | undefined) || []).map((point) => [
      String(point.symbol || ""),
      point,
    ])
  );

  const recognizedInsights = parsed.holdings
    .map((holding) =>
      buildHoldingInsight(
        holding,
        mapPoints.get(holding.symbol),
        optimizedHoldings.get(holding.symbol)
      )
    )
    .filter((insight) => mapPoints.has(insight.symbol) || optimizedHoldings.has(insight.symbol));

  const keep: Array<{ symbol: string; rationale: string }> = [];
  const reduce: Array<{ symbol: string; rationale: string }> = [];
  const exit: Array<{ symbol: string; rationale: string }> = [];

  for (const insight of recognizedInsights) {
    const targetGap = insight.targetWeightPct - insight.currentWeightPct;
    if (
      insight.finalAction === "SELL" ||
      (insight.targetWeightPct < 1 && (insight.score < -0.15 || (insight.regimeRisk ?? 0) > 0.72))
    ) {
      exit.push({
        symbol: insight.symbol,
        rationale: "signal quality is weak versus the current optimizer",
      });
      continue;
    }

    if (
      targetGap < -2 ||
      (insight.uncertainty ?? 0) > 0.09 ||
      (insight.regimeRisk ?? 0) > 0.58
    ) {
      reduce.push({
        symbol: insight.symbol,
        rationale: "weight looks heavy relative to current uncertainty and regime risk",
      });
      continue;
    }

    keep.push({
      symbol: insight.symbol,
      rationale:
        optimizedSymbols.has(insight.symbol)
          ? "it still overlaps with the optimized core"
          : "it remains acceptable as a selective non-core position",
    });
  }

  const ownedSymbols = new Set(parsed.holdings.map((holding) => holding.symbol));
  const addCandidates: Array<{ symbol: string; rationale: string }> = [];
  for (const holding of optimizedPortfolio.holdings || []) {
    const symbol = String(holding.symbol || "");
    if (!symbol || ownedSymbols.has(symbol)) {
      continue;
    }
    addCandidates.push({
      symbol,
      rationale: "it is already in the optimized portfolio sleeve",
    });
    if (addCandidates.length >= 4) {
      break;
    }
  }
  for (const point of (informationMap.darkHorsePicks as MapPoint[] | undefined) || []) {
    const symbol = String(point.symbol || "");
    if (!symbol || ownedSymbols.has(symbol) || addCandidates.some((item) => item.symbol === symbol)) {
      continue;
    }
    addCandidates.push({
      symbol,
      rationale: `symmetry dark-horse setup vs ${point.symmetry?.counterpartSymbol || "market mirror"}`,
    });
    if (addCandidates.length >= 7) {
      break;
    }
  }

  const overlapWeightPct = parsed.holdings.reduce(
    (sum, holding) => sum + (optimizedSymbols.has(holding.symbol) ? holding.weightPct : 0),
    0
  );
  const weightedUpsidePct = weightedAverage(
    parsed.holdings,
    (holding) =>
      optimizedHoldings.get(holding.symbol)?.maxUpsidePct ??
      mapPoints.get(holding.symbol)?.maxUpsidePct ??
      null,
    (holding) => holding.weightPct
  );
  const weightedUncertaintyPct = weightedAverage(
    parsed.holdings,
    (holding) =>
      optimizedHoldings.get(holding.symbol)?.uncertaintyRatio ??
      mapPoints.get(holding.symbol)?.uncertaintyRatio ??
      null,
    (holding) => holding.weightPct
  );
  const weightedDrawdownLingerDays = weightedAverage(
    parsed.holdings,
    (holding) => {
      const value =
        optimizedHoldings.get(holding.symbol)?.drawdownLingerSeconds ??
        mapPoints.get(holding.symbol)?.drawdownLingerSeconds ??
        null;
      return value != null ? value / 86_400 : null;
    },
    (holding) => holding.weightPct
  );
  const weightedSpikeSustainDays = weightedAverage(
    parsed.holdings,
    (holding) => {
      const value =
        optimizedHoldings.get(holding.symbol)?.spikeSustainSeconds ??
        mapPoints.get(holding.symbol)?.spikeSustainSeconds ??
        null;
      return value != null ? value / 86_400 : null;
    },
    (holding) => holding.weightPct
  );
  const weightedDarkHorseScore = weightedAverage(
    parsed.holdings,
    (holding) =>
      optimizedHoldings.get(holding.symbol)?.darkHorseScore ??
      mapPoints.get(holding.symbol)?.darkHorseScore ??
      null,
    (holding) => holding.weightPct
  );
  const unknownOwnedSymbols = parsed.holdings
    .filter((holding) => !mapPoints.has(holding.symbol) && !optimizedHoldings.has(holding.symbol))
    .map((holding) => holding.symbol);

  return {
    keep: keep.slice(0, 5),
    reduce: reduce.slice(0, 5),
    exit: [...exit, ...unknownOwnedSymbols.map((symbol) => ({
      symbol,
      rationale: "not recognized in the current S&P500 screen",
    }))].slice(0, 5),
    add: addCandidates.slice(0, 6),
    summary: {
      holdingsCount: parsed.holdings.length,
      recognizedHoldingsCount: recognizedInsights.length,
      overlapWeightPct,
      weightedUpsidePct,
      weightedUncertaintyPct,
      weightedDrawdownLingerDays,
      weightedSpikeSustainDays,
      weightedDarkHorseScore,
      unknownSymbols: [...new Set([...parsed.unknownSymbols, ...unknownOwnedSymbols])].slice(0, 12),
    },
    topOptimizedSummary: (optimizedPortfolio.holdings || [])
      .slice(0, 5)
      .map((holding) => `${holding.symbol} ${formatPercent(holding.portfolioWeightPct ?? holding.weightPct, 1)}`)
      .join(" | "),
    darkHorseSummary: (((informationMap.darkHorsePicks as MapPoint[] | undefined) || [])
      .slice(0, 5)
      .map(
        (point) =>
          `${point.symbol} (${point.darkHorseLabel || "candidate"} ${formatPercent(point.darkHorseScore, 0)})`
      )
      .join(" | ")) || "none",
  };
}

export async function POST(req: Request) {
  const guard = guardApiRequest(req, {
    routeKey: "portfolio-chat",
    maxBodyBytes: MAX_FILE_BYTES + 64 * 1024,
    allowedContentTypes: ["multipart/form-data"],
    rateLimit: {
      key: "portfolio-chat",
      limit: 8,
      windowMs: 5 * 60_000,
    },
  });
  if (guard) {
    return guard;
  }

  try {
    const form = await req.formData();
    const file = form.get("file");
    const promptValue = String(form.get("prompt") || "").slice(0, MAX_PROMPT_CHARS).trim();

    if (!(file instanceof File)) {
      return secureJson({ error: "CSV file is required." }, { status: 400 });
    }
    if (!ALLOWED_MIME_TYPES.has(file.type)) {
      return secureJson({ error: "Only CSV text files are supported." }, { status: 400 });
    }
    if (file.size <= 0 || file.size > MAX_FILE_BYTES) {
      return secureJson(
        { error: `CSV file must be between 1 byte and ${MAX_FILE_BYTES} bytes.` },
        { status: 400 }
      );
    }

    const csvText = await file.text();
    const parsed = parsePortfolioCsvText(csvText);

    const [informationMap, optimizedPortfolio, macroBackdrop] = await Promise.all([
      buildSp500InformationMap(false),
      buildSp500Portfolio(false),
      getMacroBackdrop(),
    ]);

    if (!informationMap.ok) {
      return secureJson(
        { error: informationMap.error || "Failed to load the information map." },
        { status: 500 }
      );
    }
    if (!optimizedPortfolio.ok) {
      return secureJson(
        { error: optimizedPortfolio.error || "Failed to load the optimized portfolio." },
        { status: 500 }
      );
    }

    const analysis = buildReconfigurationAnalysis(parsed, informationMap, optimizedPortfolio);
    const context = `
User Prompt: ${promptValue || "업로드한 포트폴리오를 재구성해줘."}
CSV Basis: ${parsed.basis}
CSV Holdings Count: ${parsed.holdings.length}
CSV Recognized Holdings Count: ${analysis.summary.recognizedHoldingsCount}
CSV Unknown Symbols: ${analysis.summary.unknownSymbols.join(" | ") || "none"}
Current Portfolio Overlap Weight Pct: ${formatPercent(analysis.summary.overlapWeightPct, 1)}
Current Portfolio Weighted Upside Pct: ${formatPercent(
      (analysis.summary.weightedUpsidePct ?? 0) * 100,
      2
    )}
Current Portfolio Weighted Uncertainty Pct: ${formatPercent(
      (analysis.summary.weightedUncertaintyPct ?? 0) * 100,
      2
    )}
Current Portfolio Weighted Drawdown Linger Days: ${
      analysis.summary.weightedDrawdownLingerDays?.toFixed(2) ?? "unknown"
    }
Current Portfolio Weighted Spike Sustain Days: ${
      analysis.summary.weightedSpikeSustainDays?.toFixed(2) ?? "unknown"
    }
Current Portfolio Weighted Dark Horse Score: ${
      analysis.summary.weightedDarkHorseScore?.toFixed(1) ?? "unknown"
    }
Keep Candidates: ${summarizeList(analysis.keep)}
Reduce Candidates: ${summarizeList(analysis.reduce)}
Exit Candidates: ${summarizeList(analysis.exit)}
Add Candidates: ${summarizeList(analysis.add)}
Optimized Portfolio Top Holdings: ${analysis.topOptimizedSummary}
Dark Horse Candidates: ${analysis.darkHorseSummary}
Macro Summary: ${macroBackdrop?.summary || "거시 환경 확인 필요"}
Portfolio Sleeves: ${
      optimizedPortfolio.allocation?.sleeves
        ?.map((sleeve) => `${sleeve.label} ${formatPercent(sleeve.weightPct, 1)}`)
        .join(" | ") || "unknown"
    }
`;

    const assistant = await runPortfolioRebalanceChatAgent(
      context,
      promptValue || "업로드한 포트폴리오를 어떻게 재구성하면 좋을지 알려줘."
    );

    return secureJson({
      ok: true,
      assistant,
      summary: analysis.summary,
      suggestions: {
        keep: analysis.keep,
        reduce: analysis.reduce,
        exit: analysis.exit,
        add: analysis.add,
      },
      security: {
        processedInMemory: true,
        filePersisted: false,
        maxFileBytes: MAX_FILE_BYTES,
        rowsProcessed: parsed.rowsProcessed,
        columnHeaders: parsed.headers.slice(0, MAX_COLUMNS),
      },
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to analyze the uploaded portfolio CSV.";
    return secureJson({ error: message }, { status: 400 });
  }
}
