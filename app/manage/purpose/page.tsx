"use client";

import { useCallback, useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

interface Agent {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
}
interface Squad {
  id: string;
  name: string;
  mode: string;
  bot_ids: string[];
}
interface Catalog {
  capabilities: { name: string; desc: string; cli: string }[];
  mcp_servers: { name: string; transport: string }[];
  agents: { name: string; kind: string }[];
  squads: { name: string; mode: string }[];
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

export default function PurposePage() {
  const [purpose, setPurpose] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [squads, setSquads] = useState<Squad[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [target, setTarget] = useState<string>(""); // "" | agent:<id> | squad:<id>
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [result, setResult] = useState<any>(null);

  const loadMeta = useCallback(async () => {
    try {
      const [a, s, c] = await Promise.all([
        api<Agent[]>("/api/chat/agents"),
        api<Squad[]>("/api/squads"),
        api<Catalog>("/api/purpose/resources"),
      ]);
      setAgents(a);
      setSquads(s);
      setCatalog(c);
    } catch (e) {
      setErr(
        e instanceof Error
          ? `${e.message} — 백엔드(:8787)가 실행 중인지 확인하세요.`
          : String(e),
      );
    }
  }, []);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  const run = async () => {
    if (!purpose.trim() || running) return;
    setRunning(true);
    setErr(null);
    setResult(null);

    // 스쿼드는 배치 실행, 단일 에이전트는 스트리밍
    if (target.startsWith("squad:")) {
      try {
        const data = await api("/api/purpose/plan", {
          method: "POST",
          body: JSON.stringify({ purpose, squad_id: target.slice(6) }),
        });
        setResult(data);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setRunning(false);
      }
      return;
    }

    const body: Record<string, string> = { purpose };
    if (target.startsWith("agent:")) body.agent_id = target.slice(6);
    const started = Date.now();
    try {
      const res = await fetch(`${API}/api/purpose/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok || !res.body) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `요청 실패 (${res.status})`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let acc = "";
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let agentMeta: any = null;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const part of parts) {
          const dataLine = part.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          let ev: any;
          try {
            ev = JSON.parse(dataLine.slice(5).trim());
          } catch {
            continue;
          }
          if (ev.type === "meta") {
            agentMeta = ev.agent;
          } else if (ev.type === "chunk") {
            acc += ev.text || "";
            setResult({
              mode: "single",
              ok: true,
              output: acc,
              agent: agentMeta,
              streaming: true,
            });
          } else if (ev.type === "error") {
            setResult({ mode: "single", ok: false, error: ev.error });
          } else if (ev.type === "done") {
            setResult({
              mode: "single",
              ok: ev.ok || !!acc,
              output: acc,
              error: ev.error,
              agent: agentMeta,
              elapsed_ms: Date.now() - started,
              resources_used: {},
            });
          }
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-12">
      <header className="mb-8">
        <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
          통합 관리 / Purpose
        </p>
        <h1 className="mt-1.5 text-[28px] font-bold tracking-tight text-white">
          Purpose 전략 엔진
        </h1>
        <p className="mt-1.5 text-sm text-gray-400">
          전략·의도를 입력하면 AI가 noslip 가용 자원으로 상담 · 전략 · 구축 가이드를 산출합니다.
        </p>
      </header>

      {err && (
        <div className="mb-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          ⚠️ {err}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* 입력 */}
        <div className="lg:col-span-2">
          <textarea
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            rows={5}
            placeholder="예) AAPL·반도체 중심으로 변동성 낮은 스윙 전략을 만들고, 매주 자동 리포트를 받고 싶다."
            className="w-full resize-none rounded-2xl border border-white/10 bg-black/30 px-4 py-3.5 text-sm leading-relaxed text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20"
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-violet-400/60"
            >
              <option value="">기본 에이전트 (claude 우선)</option>
              {agents.filter((a) => a.enabled).length > 0 && (
                <optgroup label="단일 에이전트">
                  {agents
                    .filter((a) => a.enabled)
                    .map((a) => (
                      <option key={a.id} value={`agent:${a.id}`}>
                        {a.name} ({a.kind})
                      </option>
                    ))}
                </optgroup>
              )}
              {squads.length > 0 && (
                <optgroup label="멀티봇 스쿼드">
                  {squads.map((s) => (
                    <option key={s.id} value={`squad:${s.id}`}>
                      🤖 {s.name} [{s.mode}]
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            <button
              onClick={run}
              disabled={running || !purpose.trim()}
              className="rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-5 py-2 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-40"
            >
              {running ? "분석 중…" : "전략 생성"}
            </button>
            {running && (
              <span className="text-xs text-gray-500">
                로컬 AI 에이전트 실행 — 수십 초 걸릴 수 있습니다.
              </span>
            )}
          </div>
        </div>

        {/* 가용 자원 패널 */}
        <aside className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
          <h3 className="mb-3 text-xs font-medium uppercase tracking-widest text-gray-500">
            가용 자원
          </h3>
          {!catalog ? (
            <p className="text-sm text-gray-500">불러오는 중…</p>
          ) : (
            <div className="space-y-3 text-sm">
              <ResourceRow label="핵심 능력" n={catalog.capabilities.length} />
              <ResourceRow label="MCP 서버" n={catalog.mcp_servers.length} />
              <ResourceRow label="AI 에이전트" n={catalog.agents.length} />
              <ResourceRow label="스쿼드" n={catalog.squads.length} />
              <div className="border-t border-white/[0.06] pt-3">
                <p className="mb-1.5 text-[11px] text-gray-500">능력 예시</p>
                <ul className="space-y-1">
                  {catalog.capabilities.slice(0, 4).map((c) => (
                    <li key={c.name} className="text-xs text-gray-400">
                      • {c.name}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </aside>
      </div>

      {/* 결과 */}
      {result && (
        <section className="mt-10">
          {result.mode === "squad" ? (
            <SquadResult result={result} />
          ) : result.ok ? (
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
              <div className="mb-4 flex items-center gap-2 text-xs text-gray-500">
                <span className="rounded-md bg-violet-400/15 px-2 py-0.5 text-violet-300">
                  {result.agent?.name || "에이전트"}
                </span>
                {result.streaming
                  ? "스트리밍 중…"
                  : result.elapsed_ms
                    ? `${(result.elapsed_ms / 1000).toFixed(1)}s`
                    : ""}
              </div>
              <Markdown text={result.output} />
            </div>
          ) : (
            <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-6 text-sm text-rose-200">
              {result.error}
            </div>
          )}
        </section>
      )}
    </main>
  );
}

function ResourceRow({ label, n }: { label: string; n: number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-400">{label}</span>
      <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-xs font-semibold text-white">
        {n}
      </span>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function SquadResult({ result }: { result: any }) {
  return (
    <div className="space-y-5">
      <div className="text-xs text-gray-500">
        멀티봇 스쿼드 · {result.squad?.name} [{result.mode}]
      </div>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      {(result.turns || []).map((t: any, i: number) => (
        <div
          key={i}
          className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6"
        >
          <div className="mb-3 flex items-center gap-2">
            <span className="text-base">🤖</span>
            <span className="font-semibold text-white">{t.bot_name}</span>
            <span className="text-xs text-gray-500">{t.role}</span>
          </div>
          {t.ok ? (
            <Markdown text={t.output} />
          ) : (
            <p className="text-sm text-rose-300">[오류] {t.error}</p>
          )}
        </div>
      ))}
    </div>
  );
}

/* 경량 마크다운 렌더러 (## 헤딩 / - 불릿 / 코드 인라인) */
function Markdown({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  return (
    <div className="space-y-1.5 text-sm leading-relaxed text-gray-200">
      {lines.map((line, i) => {
        if (/^#{1,6}\s/.test(line)) {
          const content = line.replace(/^#{1,6}\s/, "");
          return (
            <h3
              key={i}
              className="mt-5 mb-1 border-b border-white/[0.06] pb-1.5 text-base font-bold text-white first:mt-0"
            >
              {content}
            </h3>
          );
        }
        if (/^\s*[-*]\s/.test(line)) {
          return (
            <div key={i} className="flex gap-2 pl-1">
              <span className="text-violet-400">•</span>
              <span dangerouslySetInnerHTML={{ __html: inline(line.replace(/^\s*[-*]\s/, "")) }} />
            </div>
          );
        }
        if (line.trim() === "") return <div key={i} className="h-1.5" />;
        return (
          <p key={i} dangerouslySetInnerHTML={{ __html: inline(line) }} />
        );
      })}
    </div>
  );
}

function inline(s: string): string {
  const escaped = s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped
    .replace(/`([^`]+)`/g, '<code class="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[12px] text-violet-300">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong class="text-white">$1</strong>');
}
