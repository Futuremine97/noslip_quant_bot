import { NextResponse } from "next/server";

type RateLimitOptions = {
  key: string;
  limit: number;
  windowMs: number;
};

type GuardApiRequestOptions = {
  routeKey: string;
  maxBodyBytes: number;
  allowedContentTypes?: string[];
  rateLimit?: RateLimitOptions;
};

type RateLimitEntry = {
  count: number;
  resetAt: number;
};

const SECURITY_HEADERS: Record<string, string> = {
  "Cache-Control": "no-store, max-age=0",
  Pragma: "no-cache",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "X-Robots-Tag": "noindex, nofollow",
  "Cross-Origin-Resource-Policy": "same-origin",
};

const RATE_LIMIT_STORE = new Map<string, RateLimitEntry>();
const LOCAL_SP500_ONLY_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

function cleanupRateLimitStore(now: number) {
  for (const [key, entry] of RATE_LIMIT_STORE.entries()) {
    if (entry.resetAt <= now) {
      RATE_LIMIT_STORE.delete(key);
    }
  }
}

function normalizeOrigin(value: string | null | undefined) {
  if (!value) {
    return null;
  }

  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function getAllowedOrigins(request: Request) {
  const allowed = new Set<string>();
  const envOrigins = [
    process.env.ALLOWED_APP_ORIGINS,
    process.env.NEXT_PUBLIC_APP_ORIGIN,
    process.env.APP_BASE_URL,
    process.env.CORS_ORIGIN,
  ]
    .flatMap((value) => String(value || "").split(","))
    .map((value) => normalizeOrigin(value.trim()))
    .filter((value): value is string => Boolean(value));

  for (const origin of envOrigins) {
    allowed.add(origin);
  }

  const requestOrigin = normalizeOrigin(new URL(request.url).origin);
  if (requestOrigin) {
    allowed.add(requestOrigin);
  }

  const vercelUrl = String(process.env.VERCEL_URL || "").trim();
  if (vercelUrl) {
    const vercelOrigin = normalizeOrigin(
      vercelUrl.startsWith("http") ? vercelUrl : `https://${vercelUrl}`
    );
    if (vercelOrigin) {
      allowed.add(vercelOrigin);
    }
  }

  return allowed;
}

function getClientIp(request: Request) {
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) {
    return forwardedFor.split(",")[0]?.trim() || "unknown";
  }

  return (
    request.headers.get("x-real-ip") ||
    request.headers.get("cf-connecting-ip") ||
    "unknown"
  );
}

function applySecurityHeaders(response: NextResponse) {
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) {
    response.headers.set(key, value);
  }
  return response;
}

function rejectRequest(payload: Record<string, unknown>, status: number) {
  return applySecurityHeaders(NextResponse.json(payload, { status }));
}

function enforceRateLimit(request: Request, options: RateLimitOptions) {
  const now = Date.now();
  cleanupRateLimitStore(now);

  const ip = getClientIp(request);
  const compoundKey = `${options.key}:${ip}`;
  const current = RATE_LIMIT_STORE.get(compoundKey);

  if (!current || current.resetAt <= now) {
    RATE_LIMIT_STORE.set(compoundKey, {
      count: 1,
      resetAt: now + options.windowMs,
    });
    return null;
  }

  if (current.count >= options.limit) {
    const retryAfterSeconds = Math.max(
      1,
      Math.ceil((current.resetAt - now) / 1000)
    );
    const response = rejectRequest(
      {
        error: "Too many requests. Please wait and try again.",
        retryAfterSeconds,
      },
      429
    );
    response.headers.set("Retry-After", String(retryAfterSeconds));
    return response;
  }

  current.count += 1;
  RATE_LIMIT_STORE.set(compoundKey, current);
  return null;
}

function validateOrigin(request: Request) {
  const requestOrigin = normalizeOrigin(request.headers.get("origin"));
  if (!requestOrigin) {
    return null;
  }

  const allowedOrigins = getAllowedOrigins(request);
  if (allowedOrigins.has(requestOrigin)) {
    return null;
  }

  return rejectRequest(
    {
      error: "Origin is not allowed.",
    },
    403
  );
}

function validateContentType(request: Request, allowedContentTypes: string[]) {
  if (!allowedContentTypes.length) {
    return null;
  }

  const contentType = String(request.headers.get("content-type") || "").toLowerCase();
  if (
    allowedContentTypes.some((allowedType) =>
      contentType.includes(allowedType.toLowerCase())
    )
  ) {
    return null;
  }

  return rejectRequest(
    {
      error: "Unsupported content type.",
    },
    415
  );
}

function validateBodySize(request: Request, maxBodyBytes: number) {
  const contentLengthHeader = request.headers.get("content-length");
  if (!contentLengthHeader) {
    return null;
  }

  const contentLength = Number(contentLengthHeader);
  if (!Number.isFinite(contentLength) || contentLength <= maxBodyBytes) {
    return null;
  }

  return rejectRequest(
    {
      error: `Request body exceeds the ${maxBodyBytes}-byte limit.`,
    },
    413
  );
}

export function guardApiRequest(
  request: Request,
  options: GuardApiRequestOptions
): NextResponse | null {
  const originError = validateOrigin(request);
  if (originError) {
    return originError;
  }

  const bodySizeError = validateBodySize(request, options.maxBodyBytes);
  if (bodySizeError) {
    return bodySizeError;
  }

  const contentTypeError = validateContentType(
    request,
    options.allowedContentTypes || []
  );
  if (contentTypeError) {
    return contentTypeError;
  }

  if (options.rateLimit) {
    return enforceRateLimit(request, options.rateLimit);
  }

  return null;
}

export function secureJson(payload: unknown, init?: ResponseInit) {
  return applySecurityHeaders(NextResponse.json(payload, init));
}

export function isSp500OnlyRequest(request: Request) {
  if (
    process.env.SP500_ONLY === "true" ||
    process.env.NEXT_PUBLIC_SP500_ONLY === "true"
  ) {
    return true;
  }

  try {
    return LOCAL_SP500_ONLY_HOSTS.has(new URL(request.url).hostname);
  } catch {
    return false;
  }
}
