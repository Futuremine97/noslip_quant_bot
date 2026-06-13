"use client";

import { useCallback, useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

type Mode = "pipeline" | "parallel" | "roundtable";

interface Agent {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
}
interface Bot {
  id: string;
  name: string;
  role: string;
  agent_id: string;
  system_prompt: string;
  enabled: boolean;
}
interface Squad {
  id: string;
  name: string;
  bot_ids: string[];
  mode: Mode;
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

const MODE_DESC: Record<Mode, string> = {
  pipeline: "순차 — 직전 출력이 다음 봇 입력",
  parallel: "동시 — 각 봇이 별도 thread/process",
  roundtable: "토론 — 누적 발언 공유하며 순차",
};

export default function BotsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [bots, setBots] = useState<Bot[]>([]);
  const [squads, setSquads] = useState<Squad[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [botPanel, setBotPanel] = useState(false);
  const [squadPanel, setSquadPanel] = useState(false);
  const [runFor, setRunFor] = useState<Squad | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, b, s] = await Promise.all([
        api<Agent[]>("/api/chat/agents"),
        api<Bot[]>("/api/bots"),
        api<Squad[]>("/api/squads"),
      ]);
      setAgents(a);
      setBots(b);
      setSquads(s);
      setErr(null);
    } catch (e) {
      setErr(
        e instanceof Error
          ? `${e.message} — 백엔드(:8787)가 실행 중인지 확인하세요.`
          : String(e),
      );
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [a, b, s] = await Promise.all([
          api<Agent[]>("/api/chat/agents"),
          api<Bot[]>("/api/bots"),
          api<Squad[]>("/api/squads"),
        ]);
        if (!alive) return;
        setAgents(a);
        setBots(b);
        setSquads(s);
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

  const delBot = async (id: string) => {
    if (!confirm(`봇 '${id}' 삭제?`)) return;
    await api(`/api/bots/${id}`, { method: "DELETE" });
    load();
  };
  const delSquad = async (id: string) => {
    if (!confirm(`스쿼드 '${id}' 삭제?`)) return;
    await api(`/api/squads/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-12">
      <header className="mb-8">
        <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
          통합 관리 / 멀티봇
        </p>
        <h1 className="mt-1.5 text-[28px] font-bold tracking-tight text-white">
          멀티봇 조립
        </h1>
        <p className="mt-1.5 text-sm text-gray-400">
          봇(역할 + 에이전트)을 만들고, 스쿼드로 조립해 process/thread 단위로 실행합니다.
        </p>
      </header>

      {err && (
        <div className="mb-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          ⚠️ {err}
        </div>
      )}

      {agents.filter((a) => a.enabled).length === 0 && (
        <div className="mb-6 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          연결된 AI 에이전트가 없습니다. 먼저 <b>채팅 → 에이전트 연결</b>에서 claude 등을 연결하세요.
        </div>
      )}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        {/* 봇 */}
        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">봇 ({bots.length})</h2>
            <button
              onClick={() => setBotPanel(true)}
              className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-gray-200 transition hover:bg-white/5"
            >
              + 봇 추가
            </button>
          </div>
          {bots.length === 0 ? (
            <p className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] px-4 py-8 text-center text-sm text-gray-500">
              봇이 없습니다. 역할별 봇을 추가하세요 (예: 전략가, 아키텍트, 빌더).
            </p>
          ) : (
            <ul className="space-y-2.5">
              {bots.map((b) => (
                <li
                  key={b.id}
                  className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.03] px-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="font-medium text-white">{b.name}</div>
                    <div className="truncate text-xs text-gray-500">
                      {b.role || "역할 미지정"} · {b.agent_id}
                    </div>
                  </div>
                  <button
                    onClick={() => delBot(b.id)}
                    className="ml-3 shrink-0 rounded-lg border border-white/10 px-2.5 py-1 text-xs text-gray-400 transition hover:border-rose-500/40 hover:text-rose-300"
                  >
                    삭제
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 스쿼드 */}
        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">스쿼드 ({squads.length})</h2>
            <button
              onClick={() => setSquadPanel(true)}
              disabled={bots.length === 0}
              className="rounded-lg bg-gradient-to-r from-violet-500 to-indigo-500 px-3 py-1.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-40"
            >
              + 스쿼드 조립
            </button>
          </div>
          {squads.length === 0 ? (
            <p className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] px-4 py-8 text-center text-sm text-gray-500">
              스쿼드가 없습니다. 봇을 골라 조립하세요.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {squads.map((s) => (
                <li
                  key={s.id}
                  className="rounded-xl border border-white/[0.07] bg-white/[0.03] px-4 py-3"
                >
                  <div className="flex items-center justify-between">
                    <div className="font-medium text-white">{s.name}</div>
                    <span className="rounded-md bg-violet-400/15 px-2 py-0.5 text-[10px] text-violet-300">
                      {s.mode}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    {s.bot_ids
                      .map((id) => bots.find((b) => b.id === id)?.name || id)
                      .join(" → ")}
                  </div>
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => setRunFor(s)}
                      className="rounded-lg border border-white/10 px-3 py-1 text-xs text-gray-200 transition hover:bg-white/10"
                    >
                      실행
                    </button>
                    <button
                      onClick={() => delSquad(s.id)}
                      className="rounded-lg border border-white/10 px-3 py-1 text-xs text-gray-400 transition hover:border-rose-500/40 hover:text-rose-300"
                    >
                      삭제
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {botPanel && (
        <BotPanel
          agents={agents.filter((a) => a.enabled)}
          onClose={() => setBotPanel(false)}
          onDone={() => {
            setBotPanel(false);
            load();
          }}
        />
      )}
      {squadPanel && (
        <SquadPanel
          bots={bots}
          onClose={() => setSquadPanel(false)}
          onDone={() => {
            setSquadPanel(false);
            load();
          }}
        />
      )}
      {runFor && <RunModal squad={runFor} bots={bots} onClose={() => setRunFor(null)} />}
    </main>
  );
}

const FIELD =
  "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20";

function Shell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-white/10 bg-[#0a0b16] p-7 shadow-2xl">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 transition hover:bg-white/10 hover:text-white"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function BotPanel({
  agents,
  onClose,
  onDone,
}: {
  agents: Agent[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [agentId, setAgentId] = useState(agents[0]?.id || "");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/bots", {
        method: "POST",
        body: JSON.stringify({ name, role, agent_id: agentId, system_prompt: prompt }),
      });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell title="봇 추가" onClose={onClose}>
      <form onSubmit={submit} className="flex flex-1 flex-col">
        <div className="space-y-4">
          <Field label="이름">
            <input required value={name} onChange={(e) => setName(e.target.value)} className={FIELD} placeholder="전략가" />
          </Field>
          <Field label="역할">
            <input value={role} onChange={(e) => setRole(e.target.value)} className={FIELD} placeholder="시장·전략 분석 담당" />
          </Field>
          <Field label="실행 에이전트">
            <select required value={agentId} onChange={(e) => setAgentId(e.target.value)} className={FIELD}>
              {agents.length === 0 && <option value="">(연결된 에이전트 없음)</option>}
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.kind})
                </option>
              ))}
            </select>
          </Field>
          <Field label="시스템 프롬프트 (페르소나/지침)">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              className={FIELD}
              placeholder="당신은 보수적 리스크 관리에 강한 퀀트 전략가입니다…"
            />
          </Field>
          {err && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{err}</p>}
        </div>
        <div className="mt-auto flex gap-2 pt-8">
          <button type="button" onClick={onClose} className="flex-1 rounded-xl border border-white/10 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5">
            취소
          </button>
          <button type="submit" disabled={busy || !agentId} className="flex-1 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 hover:brightness-110 disabled:opacity-50">
            {busy ? "추가 중…" : "추가"}
          </button>
        </div>
      </form>
    </Shell>
  );
}

function SquadPanel({
  bots,
  onClose,
  onDone,
}: {
  bots: Bot[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("pipeline");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selected.length === 0) {
      setErr("봇을 1개 이상 선택하세요.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api("/api/squads", {
        method: "POST",
        body: JSON.stringify({ name, mode, bot_ids: selected }),
      });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell title="스쿼드 조립" onClose={onClose}>
      <form onSubmit={submit} className="flex flex-1 flex-col">
        <div className="space-y-4">
          <Field label="이름">
            <input required value={name} onChange={(e) => setName(e.target.value)} className={FIELD} placeholder="research-crew" />
          </Field>
          <Field label="실행 모드">
            <div className="grid grid-cols-3 gap-2">
              {(["pipeline", "parallel", "roundtable"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`rounded-lg border px-2 py-2 text-xs transition ${
                    mode === m ? "border-violet-400/60 bg-violet-500/15 text-white" : "border-white/10 text-gray-400 hover:bg-white/5"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-gray-500">{MODE_DESC[mode]}</p>
          </Field>
          <Field label={`봇 선택 (선택 순서 = 실행 순서) · ${selected.length}개`}>
            <ul className="space-y-1.5">
              {bots.map((b) => {
                const idx = selected.indexOf(b.id);
                const on = idx !== -1;
                return (
                  <li key={b.id}>
                    <button
                      type="button"
                      onClick={() => toggle(b.id)}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition ${
                        on ? "border-violet-400/60 bg-violet-500/10 text-white" : "border-white/10 text-gray-300 hover:bg-white/5"
                      }`}
                    >
                      <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] ${on ? "bg-violet-500 text-white" : "bg-white/10 text-gray-500"}`}>
                        {on ? idx + 1 : ""}
                      </span>
                      <span className="flex-1">{b.name}</span>
                      <span className="text-[11px] text-gray-500">{b.role}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Field>
          {err && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{err}</p>}
        </div>
        <div className="mt-auto flex gap-2 pt-8">
          <button type="button" onClick={onClose} className="flex-1 rounded-xl border border-white/10 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5">
            취소
          </button>
          <button type="submit" disabled={busy} className="flex-1 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 hover:brightness-110 disabled:opacity-50">
            {busy ? "조립 중…" : "조립"}
          </button>
        </div>
      </form>
    </Shell>
  );
}

function RunModal({
  squad,
  bots,
  onClose,
}: {
  squad: Squad;
  bots: Bot[];
  onClose: () => void;
}) {
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [turns, setTurns] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    if (!input.trim() || running) return;
    setRunning(true);
    setErr(null);
    setTurns(null);
    try {
      const data = await api<{ turns: unknown[] }>(`/api/squads/${squad.id}/run`, {
        method: "POST",
        body: JSON.stringify({ input }),
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setTurns((data as any).turns || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0a0b16] shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-4">
          <div>
            <h2 className="font-semibold text-white">{squad.name} 실행</h2>
            <p className="text-xs text-gray-500">
              [{squad.mode}] {squad.bot_ids.map((id) => bots.find((b) => b.id === id)?.name || id).join(" → ")}
            </p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-gray-400 hover:bg-white/10 hover:text-white" aria-label="닫기">
            ✕
          </button>
        </div>
        <div className="flex items-end gap-2 px-6 py-4">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={2}
            placeholder="과제·질문을 입력하세요…"
            className={`${FIELD} resize-none`}
          />
          <button
            onClick={run}
            disabled={running || !input.trim()}
            className="h-[44px] shrink-0 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 hover:brightness-110 disabled:opacity-40"
          >
            {running ? "실행 중…" : "실행"}
          </button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto px-6 pb-6">
          {err && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{err}</p>}
          {running && <p className="text-sm text-gray-500">봇들을 실행 중입니다… (수십 초 소요 가능)</p>}
          {turns?.map((t, i) => (
            <div key={i} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
              <div className="mb-2 flex items-center gap-2">
                <span>🤖</span>
                <span className="font-semibold text-white">{t.bot_name}</span>
                <span className="text-xs text-gray-500">{t.role}</span>
              </div>
              <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-gray-200">
                {t.ok ? t.output : `[오류] ${t.error}`}
              </pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-gray-400">{label}</span>
      {children}
    </label>
  );
}
