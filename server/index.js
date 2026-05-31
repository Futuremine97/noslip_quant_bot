"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { execFile } = require("child_process");
const { promisify } = require("util");
const { URL } = require("url");

function loadDotEnv() {
  const envPath = path.resolve(__dirname, "..", ".env");
  if (!fs.existsSync(envPath)) {
    return;
  }

  const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }

    const separatorIndex = line.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    let value = line.slice(separatorIndex + 1).trim();

    if (!key || process.env[key] !== undefined) {
      continue;
    }

    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    process.env[key] = value;
  }
}

loadDotEnv();

const PORT = Number(process.env.PORT || 8787);
const JUPITER_API_KEY = process.env.JUPITER_API_KEY || "";
const JUPITER_BASE_URL = process.env.JUPITER_BASE_URL || "https://api.jup.ag";
const ALLOWED_ORIGINS = [
  process.env.CORS_ORIGIN || "",
  process.env.CORS_ORIGINS || "",
  process.env.APP_BASE_URL || "",
  process.env.NEXT_PUBLIC_APP_ORIGIN || "",
]
  .flatMap((value) => String(value).split(","))
  .map((value) => value.trim())
  .filter(Boolean);
const RETRYABLE_STATUSES = new Set([429, 500, 502, 503, 504]);
const execFileAsync = promisify(execFile);
const TRADER_DIR = path.resolve(__dirname, "..", "services", "trader");
const TRADER_VENV_PYTHON = path.join(TRADER_DIR, ".venv", "bin", "python");
const PREDICTION_PYTHON_BIN =
  process.env.PREDICTION_PYTHON_BIN ||
  (fs.existsSync(TRADER_VENV_PYTHON) ? TRADER_VENV_PYTHON : "python3");
const PREDICTION_SCRIPT = path.join(TRADER_DIR, "predict_signal.py");
const PREDICTION_CACHE_TTL_MS = Number(
  process.env.PREDICTION_CACHE_TTL_MS || 5 * 60 * 1000
);
const MAX_JSON_BODY_BYTES = Number(process.env.MAX_JSON_BODY_BYTES || 1024 * 1024);
const RATE_LIMIT_WINDOW_MS = Number(process.env.API_RATE_LIMIT_WINDOW_MS || 60 * 1000);
const DEFAULT_RATE_LIMIT = Number(process.env.API_RATE_LIMIT || 60);
const SYMBOL_PATTERN = /^[A-Z][A-Z0-9.-]{0,9}$/;
const MINT_PATTERN = /^[1-9A-HJ-NP-Za-km-z]{32,64}$/;
const AMOUNT_PATTERN = /^\d{1,20}$/;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9:_-]{1,120}$/;
const rateLimitStore = new Map();
const predictionCache = new Map();
const SECURITY_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Permissions-Policy":
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()",
};

function normalizeOrigin(value) {
  if (!value) {
    return null;
  }

  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function getAllowedOrigin(request) {
  const requestOrigin = normalizeOrigin(request.headers.origin);
  if (!requestOrigin) {
    return null;
  }

  if (ALLOWED_ORIGINS.includes("*")) {
    return requestOrigin;
  }

  for (const allowedOrigin of ALLOWED_ORIGINS) {
    if (normalizeOrigin(allowedOrigin) === requestOrigin) {
      return requestOrigin;
    }
  }

  return null;
}

function sendJson(request, response, statusCode, payload, extraHeaders = {}) {
  const body = JSON.stringify(payload);
  const allowedOrigin = getAllowedOrigin(request);
  const headers = {
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    Vary: "Origin",
    ...SECURITY_HEADERS,
    ...extraHeaders,
  };

  if (allowedOrigin) {
    headers["Access-Control-Allow-Origin"] = allowedOrigin;
  }

  response.writeHead(statusCode, {
    ...headers,
  });
  response.end(body);
}

function validateEnv() {
  if (!JUPITER_API_KEY) {
    throw new Error("Missing JUPITER_API_KEY");
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

async function readJsonBody(request) {
  const declaredLength = Number(request.headers["content-length"] || 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_JSON_BODY_BYTES) {
    const error = new Error("Request body is too large");
    error.statusCode = 413;
    throw error;
  }

  const chunks = [];
  let totalBytes = 0;

  for await (const chunk of request) {
    totalBytes += chunk.length;
    if (totalBytes > MAX_JSON_BODY_BYTES) {
      const error = new Error("Request body is too large");
      error.statusCode = 413;
      throw error;
    }
    chunks.push(chunk);
  }

  if (!chunks.length) {
    return {};
  }

  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function getClientIp(request) {
  const forwardedFor = request.headers["x-forwarded-for"];
  if (typeof forwardedFor === "string" && forwardedFor.trim()) {
    return forwardedFor.split(",")[0].trim();
  }

  if (typeof request.headers["x-real-ip"] === "string" && request.headers["x-real-ip"].trim()) {
    return request.headers["x-real-ip"].trim();
  }

  return request.socket?.remoteAddress || "unknown";
}

function enforceRateLimit(request, response, key, limit = DEFAULT_RATE_LIMIT) {
  const now = Date.now();
  for (const [entryKey, entry] of rateLimitStore.entries()) {
    if (entry.resetAt <= now) {
      rateLimitStore.delete(entryKey);
    }
  }

  const compoundKey = `${key}:${getClientIp(request)}`;
  const existing = rateLimitStore.get(compoundKey);

  if (!existing || existing.resetAt <= now) {
    rateLimitStore.set(compoundKey, {
      count: 1,
      resetAt: now + RATE_LIMIT_WINDOW_MS,
    });
    return false;
  }

  if (existing.count >= limit) {
    const retryAfterSeconds = Math.max(1, Math.ceil((existing.resetAt - now) / 1000));
    sendJson(
      request,
      response,
      429,
      {
        error: "Too many requests. Please wait and try again.",
        retryAfterSeconds,
      },
      {
        "Retry-After": String(retryAfterSeconds),
      }
    );
    return true;
  }

  existing.count += 1;
  rateLimitStore.set(compoundKey, existing);
  return false;
}

function requiredPatternString(body, fieldName, pattern, maxLength = 256) {
  const value = requiredString(body, fieldName);
  if (value.length > maxLength || !pattern.test(value)) {
    throw new Error(`Invalid ${fieldName}`);
  }
  return value;
}

async function callJupiter(path, options = {}) {
  validateEnv();

  let attempt = 0;

  while (attempt < 3) {
    const response = await fetch(`${JUPITER_BASE_URL}${path}`, {
      ...options,
      headers: {
        "x-api-key": JUPITER_API_KEY,
        ...(options.headers || {}),
      },
    });

    if (!RETRYABLE_STATUSES.has(response.status) || attempt === 2) {
      return response;
    }

    await sleep(350 * 2 ** attempt);
    attempt += 1;
  }

  throw new Error("Jupiter request failed after retries");
}

async function runPrediction(symbol) {
  const cacheKey = String(symbol || "").trim().toUpperCase();
  if (!SYMBOL_PATTERN.test(cacheKey)) {
    throw new Error("Invalid symbol");
  }
  const cached = predictionCache.get(cacheKey);
  if (cached && Date.now() - cached.createdAt < PREDICTION_CACHE_TTL_MS) {
    return cached.payload;
  }

  const { stdout, stderr } = await execFileAsync(
    PREDICTION_PYTHON_BIN,
    [PREDICTION_SCRIPT, "--symbol", cacheKey],
    {
      cwd: TRADER_DIR,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
      maxBuffer: 1024 * 1024 * 4,
    }
  );

  const trimmedStdout = stdout.trim();
  if (!trimmedStdout) {
    throw new Error(stderr.trim() || "Prediction script returned no output");
  }

  const lines = trimmedStdout.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const jsonLine = [...lines].reverse().find((l) => l.startsWith("{") && l.endsWith("}"));
  if (!jsonLine) {
    throw new Error(`Prediction script returned no JSON payload. Raw stdout: ${trimmedStdout}`);
  }

  const payload = JSON.parse(jsonLine);
  predictionCache.set(cacheKey, {
    createdAt: Date.now(),
    payload,
  });
  return payload;
}

function requiredString(body, fieldName) {
  const value = body?.[fieldName];

  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Missing ${fieldName}`);
  }

  return value.trim();
}

async function handleTokenSearch(request, requestUrl, response) {
  const query = requestUrl.searchParams.get("query") || "";

  if (query.trim().length < 2) {
    sendJson(request, response, 200, []);
    return;
  }

  const upstream = await callJupiter(
    `/tokens/v2/search?query=${encodeURIComponent(query.trim())}`
  );
  const payload = await upstream.json().catch(() => []);

  if (!upstream.ok) {
    sendJson(request, response, upstream.status, {
      error: payload?.message || "Token search failed",
    });
    return;
  }

  sendJson(request, response, 200, payload);
}

async function handleOrder(request, response) {
  const body = await readJsonBody(request);
  const inputMint = requiredPatternString(body, "inputMint", MINT_PATTERN, 64);
  const outputMint = requiredPatternString(body, "outputMint", MINT_PATTERN, 64);
  const amount = requiredPatternString(body, "amount", AMOUNT_PATTERN, 20);
  const taker = requiredPatternString(body, "taker", MINT_PATTERN, 64);

  const searchParams = new URLSearchParams({
    inputMint,
    outputMint,
    amount,
    taker,
  });

  const upstream = await callJupiter(`/swap/v2/order?${searchParams.toString()}`);
  const payload = await upstream.json().catch(() => ({}));

  if (!upstream.ok) {
    sendJson(request, response, upstream.status, {
      error: payload?.message || payload?.error || "Jupiter order failed",
      details: payload,
    });
    return;
  }

  sendJson(request, response, 200, payload);
}

async function handleExecute(request, response) {
  const body = await readJsonBody(request);
  const signedTransaction = requiredString(body, "signedTransaction");
  const requestId = requiredPatternString(body, "requestId", REQUEST_ID_PATTERN, 120);

  if (signedTransaction.length > 2048 * 1024) {
    const error = new Error("signedTransaction is too large");
    error.statusCode = 413;
    throw error;
  }

  const payload = {
    signedTransaction,
    requestId,
  };

  if (body.lastValidBlockHeight !== undefined) {
    payload.lastValidBlockHeight = body.lastValidBlockHeight;
  }

  const upstream = await callJupiter("/swap/v2/execute", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const executePayload = await upstream.json().catch(() => ({}));

  if (!upstream.ok) {
    sendJson(request, response, upstream.status, {
      error:
        executePayload?.message ||
        executePayload?.error ||
        "Jupiter execute failed",
      details: executePayload,
    });
    return;
  }

  sendJson(request, response, 200, executePayload);
}

async function handlePrediction(request, requestUrl, response) {
  const symbol = (requestUrl.searchParams.get("symbol") || "").trim();

  if (!symbol) {
    sendJson(request, response, 400, {
      error: "Missing symbol",
    });
    return;
  }

  try {
    const payload = await runPrediction(symbol);
    sendJson(request, response, 200, payload);
  } catch (error) {
    const statusCode = error.statusCode || 500;
    sendJson(request, response, statusCode, {
      error: error.message || "Prediction failed",
    });
  }
}

const server = http.createServer(async (request, response) => {
  const requestUrl = new URL(request.url, `http://${request.headers.host}`);
  const requestOrigin = normalizeOrigin(request.headers.origin);
  if (requestOrigin && !getAllowedOrigin(request) && ALLOWED_ORIGINS.length > 0) {
    sendJson(request, response, 403, {
      error: "Origin is not allowed",
    });
    return;
  }

  if (request.method === "OPTIONS") {
    const allowedOrigin = getAllowedOrigin(request);
    const headers = {
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
      Vary: "Origin",
      ...SECURITY_HEADERS,
    };
    if (allowedOrigin) {
      headers["Access-Control-Allow-Origin"] = allowedOrigin;
    }
    response.writeHead(204, headers);
    response.end();
    return;
  }

  try {
    if (request.method !== "GET" && request.method !== "POST") {
      sendJson(request, response, 405, {
        error: "Method not allowed",
      });
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/api/health") {
      sendJson(request, response, 200, {
        ok: true,
        hasApiKey: Boolean(JUPITER_API_KEY),
      });
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/api/tokens/search") {
      if (enforceRateLimit(request, response, "tokens-search", 40)) {
        return;
      }
      await handleTokenSearch(request, requestUrl, response);
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/api/prediction/signal") {
      if (enforceRateLimit(request, response, "prediction-signal", 20)) {
        return;
      }
      await handlePrediction(request, requestUrl, response);
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/api/swap/order") {
      if (enforceRateLimit(request, response, "swap-order", 25)) {
        return;
      }
      await handleOrder(request, response);
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/api/swap/execute") {
      if (enforceRateLimit(request, response, "swap-execute", 15)) {
        return;
      }
      await handleExecute(request, response);
      return;
    }

    sendJson(request, response, 404, {
      error: "Not found",
    });
  } catch (error) {
    const statusCode = error.statusCode || 500;
    sendJson(request, response, statusCode, {
      error: error.message || "Internal server error",
    });
  }
});

server.listen(PORT, () => {
  console.log(`Jupiter proxy listening on http://localhost:${PORT}`);
});
