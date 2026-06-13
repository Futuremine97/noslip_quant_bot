"use client";

import { useCallback, useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

interface Settings {
  enabled: boolean;
  idle_seconds: number;
  prefer_local: boolean;
}
interface LogEntry {
  ts: string;
  question: string;
  agent: string;
  local: boolean;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.detail || `요청 실패 (${res.status})`);
  return body.data as T;
}

export default function CompanionPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadLog = useCallback(async () => {
    try {
      setLog(await api<LogEntry[]>("/api/companion/log?limit=30"));
    } catch {
      /* 로그는 없을 수 있음 */
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, l] = await Promise.all([
          api<Settings>("/api/companion/settings"),
          api<LogEntry[]>("/api/companion/log?limit=30").catch(() => [] as LogEntry[]),
        ]);
        if (!alive) return;
        setSettings(s);
        setLog(l);
      } catch (e) {
        if (alive)
          setErr(
            e instanceof Error
              ? `${e.message} — 백엔드(:8787)가 실행 중인지 확인하세요.`
              : String(e),
          );
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const patch = async (p: Partial<Settings>) => {
    if (!settings) return;
    const next = { ...settings, ...p };
    setSettings(next);
    setSaving(true);
    setErr(null);
    try {
      setSettings(await api<Settings>("/api/companion/settings", {
        method: "PUT",
        body: JSON.stringify(p),
      }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-4xl px-8 py-12">
      <header className="mb-8">
        <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
          통합 관리 / 동반 에이전트
        </p>
        <h1 className="mt-1.5 text-[28px] font-bold tracking-tight text-white">
          역질문 / 답변 (Companion)
        </h1>
        <p className="mt-1.5 text-sm text-gray-400">
          사용자가 가만히 있으면 에이전트가 먼저 역질문을 던지는 기능입니다. 언제든 끌 수 있습니다.
        </p>
      </header>

      {err && (
        <div className="mb-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          ⚠️ {err}
        </div>
      )}

      {!settings ? (
        <p className="text-sm text-gray-500">불러오는 중…</p>
      ) : (
        <>
          {/* 마스터 토글 */}
          <div
            className={`mb-5 flex items-center justify-between rounded-2xl border p-5 transition ${
              settings.enabled
                ? "border-violet-400/40 bg-gradient-to-br from-violet-500/15 to-indigo-500/5"
                : "border-white/[0.07] bg-white/[0.03]"
            }`}
          >
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-semibold text-white">역질문 기능</span>
                <span
                  className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${
                    settings.enabled
                      ? "bg-emerald-400/15 text-emerald-300"
                      : "bg-gray-500/20 text-gray-400"
                  }`}
                >
                  {settings.enabled ? "켜짐" : "꺼짐"}
                </span>
              </div>
              <p className="mt-1 text-sm text-gray-400">
                {settings.enabled
                  ? `유휴 ${settings.idle_seconds}초 후 에이전트가 먼저 질문합니다.`
                  : "현재 비활성 — 에이전트가 먼저 말 걸지 않습니다."}
              </p>
            </div>
            <Toggle on={settings.enabled} onChange={(v) => patch({ enabled: v })} big />
          </div>

          {/* 세부 설정 */}
          <div
            className={`grid grid-cols-1 gap-4 sm:grid-cols-2 ${
              settings.enabled ? "" : "pointer-events-none opacity-40"
            }`}
          >
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-300">유휴 임계값</span>
                <span className="font-mono text-sm text-violet-300">
                  {settings.idle_seconds}s
                </span>
              </div>
              <input
                type="range"
                min={10}
                max={300}
                step={5}
                value={settings.idle_seconds}
                onChange={(e) => patch({ idle_seconds: Number(e.target.value) })}
                className="w-full accent-violet-500"
              />
              <p className="mt-1 text-xs text-gray-500">
                이 시간 동안 입력이 없으면 역질문을 던집니다.
              </p>
            </div>

            <div className="flex items-center justify-between rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
              <div>
                <span className="text-sm font-medium text-gray-300">로컬 AI 우선</span>
                <p className="mt-1 text-xs text-gray-500">
                  가능하면 로컬 표시 에이전트를 먼저 사용합니다.
                </p>
              </div>
              <Toggle on={settings.prefer_local} onChange={(v) => patch({ prefer_local: v })} />
            </div>
          </div>

          <p className="mt-3 text-xs text-gray-600">
            {saving ? "저장 중…" : "변경 즉시 저장됩니다. 실행 중인 CLI도 다음 주기에 반영됩니다."}
          </p>

          {/* 역질문 로그 시각화 */}
          <div className="mt-10">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">최근 역질문</h2>
              <button
                onClick={loadLog}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/5"
              >
                새로고침
              </button>
            </div>
            {log.length === 0 ? (
              <p className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] px-4 py-10 text-center text-sm text-gray-500">
                아직 기록된 역질문이 없습니다. CLI에서 <code>noslip companion</code> 실행 후
                가만히 있어 보세요.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {log.map((e, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.03] px-4 py-3"
                  >
                    <span className="mt-0.5 text-base">🤔</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm leading-relaxed text-gray-200">{e.question}</p>
                      <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-500">
                        <span>{new Date(e.ts).toLocaleString()}</span>
                        <span>·</span>
                        <span>{e.agent}</span>
                        {e.local && (
                          <span className="rounded bg-emerald-400/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
                            로컬
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </main>
  );
}

function Toggle({
  on,
  onChange,
  big,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  big?: boolean;
}) {
  const w = big ? "h-7 w-12" : "h-6 w-11";
  const knob = big ? "h-5 w-5" : "h-4 w-4";
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative ${w} shrink-0 rounded-full transition ${
        on ? "bg-gradient-to-r from-violet-500 to-indigo-500" : "bg-white/15"
      }`}
      aria-pressed={on}
    >
      <span
        className={`absolute top-1 ${knob} rounded-full bg-white transition-all ${
          on ? (big ? "left-6" : "left-6") : "left-1"
        }`}
      />
    </button>
  );
}
