'use server'

import fs from 'fs';
import path from 'path';

/**
 * Birdeye OHLCV Item from API
 */
interface BirdeyeOHLCVItem {
    unixTime: number;
    o: number; // Open
    h: number; // High
    l: number; // Low
    c: number; // Close
    v: number; // Volume
}

interface BirdeyeResponse {
    success: boolean;
    data: {
        items: BirdeyeOHLCVItem[];
    };
}

/**
 * Format expected by the Prophet model in the trader service
 */
export interface ProphetDataPoint {
    ds: string;    // ISO String
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
}

type CloseSeriesPoint = {
    ds: string;
    close: number;
};

const PROJECT_ROOT = process.cwd();
const STABLE_SYMBOLS = new Set(['USDC', 'USDT', 'USDE', 'USD', 'PYUSD', 'USDS', 'USDF', 'USDG', 'USDTB', 'USD0', 'USDD', 'USDY', 'RLUSD']);
const LOCAL_PRICE_DATASETS: Record<string, string> = {
    SOL: path.join(PROJECT_ROOT, 'data', 'historical', 'sol_usd_1m.csv'),
    ETH: path.join(PROJECT_ROOT, 'data', 'historical', 'eth_usd_1m.csv'),
};
const REMOTE_PRICE_TICKERS: Record<string, string> = {
    SOL: 'SOLUSDT',
    ETH: 'ETHUSDT',
    WETH: 'ETHUSDT',
    BTC: 'BTCUSDT',
    WBTC: 'BTCUSDT',
    BNB: 'BNBUSDT',
};
const KNOWN_MINT_SYMBOLS: Record<string, string> = {
    So11111111111111111111111111111111111111112: 'SOL',
    EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v: 'USDC',
    Es9vMFrzaCERmJfrF4H2FYDciUunG4YwN8DqSCDtnznX: 'USDT',
};

// In-memory cache for Birdeye data (persists across Lambda invocations within same instance)
const birdeyeCache = new Map<string, { data: BirdeyeOHLCVItem[]; timestamp: number }>();
const CACHE_TTL_MS = 60 * 1000; // 60 seconds
const remoteSeriesCache = new Map<string, { data: CloseSeriesPoint[]; timestamp: number }>();


const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

function parseRouteSymbols(symbolHint?: string): [string | null, string | null] {
    if (!symbolHint) {
        return [null, null];
    }

    const parts = symbolHint
        .split(/[→>-]+/)
        .map((part) => part.trim().toUpperCase())
        .filter(Boolean);

    return [parts[0] || null, parts[1] || null];
}

function resolveFallbackSymbol(address: string, hint?: string | null): string | null {
    const normalizedHint = hint?.trim().toUpperCase() || null;
    if (normalizedHint && (normalizedHint in LOCAL_PRICE_DATASETS || STABLE_SYMBOLS.has(normalizedHint))) {
        return normalizedHint;
    }

    return KNOWN_MINT_SYMBOLS[address] || normalizedHint;
}

function loadLocalCloseSeries(symbol: string, minutes: number): CloseSeriesPoint[] {
    const filePath = LOCAL_PRICE_DATASETS[symbol];
    if (!filePath || !fs.existsSync(filePath)) {
        return [];
    }

    const lines = fs.readFileSync(filePath, 'utf8').trim().split(/\r?\n/);
    if (lines.length < 2) {
        return [];
    }

    const header = lines[0].split(',');
    const dsIndex = header.indexOf('ds');
    const closeIndex = header.indexOf('close');
    if (dsIndex === -1 || closeIndex === -1) {
        return [];
    }

    return lines
        .slice(1)
        .map((line) => line.split(','))
        .map((parts) => ({
            ds: parts[dsIndex],
            close: Number(parts[closeIndex]),
        }))
        .filter((point) => point.ds && Number.isFinite(point.close))
        .slice(-minutes);
}

async function fetchRemoteCloseSeries(symbol: string, minutes: number): Promise<CloseSeriesPoint[]> {
    const remoteTicker = REMOTE_PRICE_TICKERS[symbol];
    if (!remoteTicker) {
        return [];
    }

    const cacheKey = `${remoteTicker}_${minutes}`;
    const cached = remoteSeriesCache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
        return cached.data;
    }

    try {
        const limit = Math.min(Math.max(minutes + 20, 120), 1000);
        const url = `https://api.binance.com/api/v3/klines?symbol=${remoteTicker}&interval=1m&limit=${limit}`;
        const response = await fetch(url, {
            method: 'GET',
            next: { revalidate: 60 },
        });

        if (!response.ok) {
            console.error(`[Fallback Series] Binance error for ${symbol} (${response.status})`);
            return [];
        }

        const payload = await response.json() as Array<[number, string, string, string, string]>;
        const series = payload
            .map((row) => ({
                ds: new Date(Number(row[0])).toISOString(),
                close: Number(row[4]),
            }))
            .filter((point) => point.ds && Number.isFinite(point.close))
            .slice(-minutes);

        remoteSeriesCache.set(cacheKey, {
            data: series,
            timestamp: Date.now(),
        });

        if (remoteSeriesCache.size > 100) {
            const firstKey = remoteSeriesCache.keys().next().value;
            if (firstKey) {
                remoteSeriesCache.delete(firstKey);
            }
        }

        return series;
    } catch (error) {
        console.error(`[Fallback Series] Failed to fetch Binance series for ${symbol}:`, error);
        return [];
    }
}

async function loadReferenceCloseSeries(
    symbol: string,
    minutes: number,
    preferRemote: boolean = false
): Promise<CloseSeriesPoint[]> {
    if (preferRemote) {
        const remoteSeries = await fetchRemoteCloseSeries(symbol, minutes);
        if (remoteSeries.length > 0) {
            return remoteSeries;
        }
    }

    const localSeries = loadLocalCloseSeries(symbol, minutes);
    if (localSeries.length > 0) {
        return localSeries;
    }

    return fetchRemoteCloseSeries(symbol, minutes);
}

function buildSyntheticProphetSeries(closes: CloseSeriesPoint[]): ProphetDataPoint[] {
    return closes.map((point) => ({
        ds: new Date(point.ds).toISOString(),
        open: point.close,
        high: point.close,
        low: point.close,
        close: point.close,
        volume: 0,
    }));
}

async function buildLocalPairFallback(
    inputSymbol: string | null,
    outputSymbol: string | null,
    minutes: number
): Promise<ProphetDataPoint[]> {
    if (!inputSymbol || !outputSymbol) {
        return [];
    }

    if (STABLE_SYMBOLS.has(inputSymbol) && STABLE_SYMBOLS.has(outputSymbol)) {
        return [];
    }

    const inputHasLocalDataset = Boolean(inputSymbol && LOCAL_PRICE_DATASETS[inputSymbol]);
    const outputHasLocalDataset = Boolean(outputSymbol && LOCAL_PRICE_DATASETS[outputSymbol]);
    const inputHasRemoteSeries = Boolean(inputSymbol && REMOTE_PRICE_TICKERS[inputSymbol]);
    const outputHasRemoteSeries = Boolean(outputSymbol && REMOTE_PRICE_TICKERS[outputSymbol]);
    const preferRemoteAlignment =
        !STABLE_SYMBOLS.has(inputSymbol) &&
        !STABLE_SYMBOLS.has(outputSymbol) &&
        inputHasRemoteSeries &&
        outputHasRemoteSeries &&
        (!inputHasLocalDataset || !outputHasLocalDataset);

    const inputSeries = STABLE_SYMBOLS.has(inputSymbol)
        ? []
        : await loadReferenceCloseSeries(inputSymbol, minutes + 20, preferRemoteAlignment);
    const outputSeries = STABLE_SYMBOLS.has(outputSymbol)
        ? []
        : await loadReferenceCloseSeries(outputSymbol, minutes + 20, preferRemoteAlignment);

    if (!STABLE_SYMBOLS.has(inputSymbol) && inputSeries.length === 0) {
        return [];
    }

    if (!STABLE_SYMBOLS.has(outputSymbol) && outputSeries.length === 0) {
        return [];
    }

    if (STABLE_SYMBOLS.has(outputSymbol)) {
        return buildSyntheticProphetSeries(inputSeries.slice(-minutes));
    }

    if (STABLE_SYMBOLS.has(inputSymbol)) {
        return buildSyntheticProphetSeries(
            outputSeries
                .slice(-minutes)
                .map((point) => ({
                    ds: point.ds,
                    close: point.close === 0 ? NaN : 1 / point.close,
                }))
                .filter((point) => Number.isFinite(point.close))
        );
    }

    const outputMap = new Map(outputSeries.map((point) => [point.ds, point.close]));
    let ratioSeries = inputSeries
        .map((point) => {
            const outputClose = outputMap.get(point.ds);
            if (!Number.isFinite(outputClose) || !outputClose) {
                return null;
            }

            return {
                ds: point.ds,
                close: point.close / outputClose,
            };
        })
        .filter((point): point is CloseSeriesPoint => point !== null)
        .slice(-minutes);

    if (ratioSeries.length < 100) {
        const pairedLength = Math.min(inputSeries.length, outputSeries.length, minutes);
        ratioSeries = Array.from({ length: pairedLength }, (_, index) => {
            const inputPoint = inputSeries[inputSeries.length - pairedLength + index];
            const outputPoint = outputSeries[outputSeries.length - pairedLength + index];

            if (!inputPoint || !outputPoint || !Number.isFinite(outputPoint.close) || !outputPoint.close) {
                return null;
            }

            return {
                ds: inputPoint.ds,
                close: inputPoint.close / outputPoint.close,
            };
        })
            .filter((point): point is CloseSeriesPoint => point !== null)
            .slice(-minutes);
    }

    return buildSyntheticProphetSeries(ratioSeries);
}

/**
 * Fetches raw OHLCV from Birdeye for a single token
 */
export async function fetchTokenHistory(
    address: string,
    minutes: number = 30,
    chain: string = "solana"
): Promise<BirdeyeOHLCVItem[]> {
    // Check cache first
    const cacheKey = `${address}_${minutes}`;
    const cached = birdeyeCache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
        return cached.data;
    }

    const BIRDEYE_API_KEY = process.env.BIRDEYE_API_KEY || "";
    const now = Math.floor(Date.now() / 1000);
    const timeFrom = now - (minutes * 60);

    const url = `https://public-api.birdeye.so/defi/ohlcv?address=${address}&type=1m&time_from=${timeFrom}&time_to=${now}`;

    let attempts = 0;
    while (attempts < 5) {
        try {
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'X-API-KEY': BIRDEYE_API_KEY,
                    'x-chain': chain
                },
                next: { revalidate: 60 } // Cache for 1 minute
            });

            if (response.status === 429) {
                console.warn(`[Birdeye] 429 Rate Limited. Sleeping ${(attempts + 1) * 3}s...`);
                // More aggressive backoff: 3s, 6s, 9s, 12s, 15s
                await sleep(3000 * (attempts + 1));
                attempts++;
                continue;
            }

            if (!response.ok) {
                console.error(`Birdeye API Error (${response.status}):`, await response.text());
                return [];
            }

            const resData: BirdeyeResponse = await response.json();

            if (resData.success && resData.data?.items) {
                // Store in cache
                birdeyeCache.set(cacheKey, {
                    data: resData.data.items,
                    timestamp: Date.now()
                });
                // Limit cache size
                if (birdeyeCache.size > 100) {
                    const firstKey = birdeyeCache.keys().next().value;
                    if (firstKey) birdeyeCache.delete(firstKey);
                }
                return resData.data.items;
            }

            return [];
        } catch (error) {
            console.error(`Error fetching Birdeye data for ${address}:`, error);
            return [];
        }
    }
    return [];
}

/**
 * Generates synthetic Ratio OHLCV data for a swap pair for Prophet model
 * Returns a minimum of 110 rows to satisfy model requirements
 */
export async function getStepDataForProphet(
    inputMint: string,
    outputMint: string,
    minutes: number = 110, // Minimum 100+ for Prophet reliability
    symbolHint?: string
): Promise<ProphetDataPoint[]> {
    // Fetch slightly more to ensure overlap
    const [inputHistory, outputHistory] = await Promise.all([
        fetchTokenHistory(inputMint, minutes + 10),
        fetchTokenHistory(outputMint, minutes + 10)
    ]);

    if (!inputHistory.length || !outputHistory.length) {
        const [inputHint, outputHint] = parseRouteSymbols(symbolHint);
        const fallbackInputSymbol = resolveFallbackSymbol(inputMint, inputHint);
        const fallbackOutputSymbol = resolveFallbackSymbol(outputMint, outputHint);
        const fallbackData = await buildLocalPairFallback(fallbackInputSymbol, fallbackOutputSymbol, minutes);

        if (fallbackData.length >= 100) {
            console.warn(
                `[Birdeye] Falling back to local pair data for ${fallbackInputSymbol || inputMint} -> ${fallbackOutputSymbol || outputMint}.`
            );
            return fallbackData;
        }

        return [];
    }

    // Create a map for O(1) alignment
    const outputMap = new Map<number, BirdeyeOHLCVItem>();
    outputHistory.forEach(item => outputMap.set(item.unixTime, item));

    // Calculate ratio and map to Prophet format
    const prophetData: ProphetDataPoint[] = inputHistory
        .map(inToken => {
            const outToken = outputMap.get(inToken.unixTime);
            if (!outToken || outToken.c === 0) return null;

            return {
                ds: new Date(inToken.unixTime * 1000).toISOString(),
                open: inToken.o / outToken.o,
                high: inToken.h / outToken.l,
                low: inToken.l / outToken.h,
                close: inToken.c / outToken.c,
                volume: inToken.v
            } as ProphetDataPoint;
        })
        .filter((item): item is ProphetDataPoint => item !== null)
        .slice(-minutes);

    return prophetData;
}
