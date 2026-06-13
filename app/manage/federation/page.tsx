"use client";

import { useCallback, useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

type Stance = "agree" | "conditional" | "decline" | "unknown";
type Status = "proposed" | "approved" | "rejected";

interface Agent {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
}
interface Bot {
  id: string;
  name: string;
}
interface Vote {
  bot_id: string;
  bot_name: string;
  stance: Stance;
  comment: string;
}
interface Turn {
  bot_name: string;
  role?: string;
  ok: boolean;
  output: string;
  error: string;
}
interface Proposal {
  id: string;
  goal: string;
  name: string;
  rationale: string;
  member_bot_ids: string[];
  mode: string;
  expected_synergy: string;
  status: Status;
  votes: Vote[];
  created_squad_id: string | null;
  run_status: string | null;
  run_input: string | null;
  run_turns: Turn[];
}

const STANCE: Record<Stance, { label: string; cls: string }> = {
  agree: { label: "찬성", cls: "bg-emerald-400/15 text-emerald-300" },
  conditional: { label: "조건부", cls: "bg-amber-400/15 text-amber-300" },
  decline: { label: "거절", cls: "bg-rose-400/15 text-rose-300" },
  unknown: { label: "미상", cls: "bg-gray-400/15 text-gray-300" },
};
const STATUS: Record<Status, { label: string; cls: string }> = {
  proposed: { label: "제안됨", cls: "bg-sky-400/15 text-sky-300" },
  approved: { label: "승인됨", cls: "bg-emerald-400/15 text-emerald-300" },
  rejected: { label: "거부됨", cls: "bg-gray-500/20 text-gray-400" },
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.detail || `요청 실패 (${res.status})`);
  return body.data as T;
}

export default function FederationPage() {
  const [goal, setGoal] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [bots, setBots] = useState<Bot[]>([]);
  const [agentId, setAgentId] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [proposing, setProposing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [autoRun, setAutoRun] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const botName = (id: string) => bots.find((b) => b.id === id)?.name || id;

  const reload = useCallback(async () => {
    const [p, b, a] = await Promise.all([
      api<Proposal[]>("/api/federation/proposals"),
      api<Bot[]>("/api/bots"),
      api<Agent[]>("/api/chat/agents"),
    ]);
    setProposals(p);
    setBots(b);
    setAgents(a);
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [p, b, a] = await Promise.all([
          api<Proposal[]>("/api/federation/proposals"),
          api<Bot[]>("/api/bots"),
          api<Agent[]>("/api/chat/agents"),
        ]);
        if (!alive) return;
        setProposals(p);
        setBots(b);
        setAgents(a);
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

  const propose = async () => {
    if (!goal.trim() || proposing) return;
    setProposing(true);
    setErr(null);
    try {
      const body: Record<string, string> = { goal };
      if (agentId) body.agent_id = agentId;
      await api("/api/federation/propose", {
        method: "POST",
        body: JSON.stringify(body),
      });
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setProposing(false);
    }
  };

  const poll = async (id: string) => {
    setBusyId(id);
    setErr(null);
    try {
      await api(`/api/federation/proposals/${id}/poll`, { method: "POST" });
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const decide = async (id: string, decision: "approve" | "reject") => {
    if (decision === "approve") {
      const msg = autoRun
        ? "이 연합을 승인하고 스쿼드를 즉시 실행할까요?"
        : "이 연합을 승인하고 스쿼드로 확정할까요?";
      if (!confirm(msg)) return;
    }
    setBusyId(id);
    setErr(null);
    try {
      await api(`/api/federation/proposals/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({
          decision,
          auto_run: decision === "approve" && autoRun,
        }),
      });
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const runApproved = async (id: string, input: string) => {
    setBusyId(id);
    setErr(null);
    try {
      await api(`/api/federation/proposals/${id}/run`, {
        method: "POST",
        body: JSON.stringify({ input: input || undefined }),
      });
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (id: string) => {
    if (!confirm("제안을 삭제할까요?")) return;
    await api(`/api/federation/proposals/${id}`, { method: "DELETE" });
    await reload();
  };

  return (
    <main className="mx-auto w-full max-w-5xl px-8 py-12">
      <header className="mb-8">
        <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
          통합 관리 / 연합
        </p>
        <h1 className="mt-1.5 text-[28px] font-bold tracking-tight text-white">
          연합 오케스트레이터
        </h1>
        <p className="mt-1.5 text-sm text-gray-400">
          AI가 봇 조합으로 연합 전략을 <b className="text-gray-300">역제안</b>하고, 각 봇의 의견을 모읍니다.
          최종 채택은 <b className="text-violet-300">사람의 승인</b>으로만 확정됩니다.
        </p>
      </header>

      {err && (
        <div className="mb-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          ⚠️ {err}
        </div>
      )}

      {/* 목표 입력 */}
      <div className="mb-10 rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
        <label className="mb-1.5 block text-xs font-medium text-gray-400">
          목표 (이 목표를 위해 봇 연합을 역제안합니다)
        </label>
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={2}
          placeholder="예) 반도체 섹터 진입 타이밍을 다각도로 분석하고 리스크까지 점검하는 전략"
          className="w-full resize-none rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-violet-400/60"
          >
            <option value="">오케스트레이터: 기본(claude 우선)</option>
            {agents
              .filter((a) => a.enabled)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.kind})
                </option>
              ))}
          </select>
          <button
            onClick={propose}
            disabled={proposing || !goal.trim()}
            className="rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-5 py-2 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-40"
          >
            {proposing ? "역제안 생성 중…" : "연합 역제안"}
          </button>
          {bots.length === 0 && (
            <span className="text-xs text-amber-300">
              먼저 멀티봇에서 봇을 등록하세요.
            </span>
          )}
        </div>
      </div>

      {/* 제안 목록 */}
      <h2 className="mb-4 text-lg font-semibold text-white">
        제안 {proposals.length > 0 && `(${proposals.length})`}
      </h2>
      {proposals.length === 0 ? (
        <p className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] px-4 py-10 text-center text-sm text-gray-500">
          아직 제안이 없습니다. 목표를 입력하고 역제안을 생성하세요.
        </p>
      ) : (
        <ul className="space-y-4">
          {proposals.map((p) => (
            <li
              key={p.id}
              className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-white">{p.name}</h3>
                    <span className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${STATUS[p.status].cls}`}>
                      {STATUS[p.status].label}
                    </span>
                    <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[10px] text-gray-300">
                      {p.mode}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-gray-500">목표: {p.goal}</p>
                </div>
                <button
                  onClick={() => remove(p.id)}
                  className="text-xs text-gray-500 transition hover:text-rose-300"
                >
                  삭제
                </button>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {p.member_bot_ids.length === 0 ? (
                  <span className="text-xs text-amber-300">멤버 없음 — 검토 필요</span>
                ) : (
                  p.member_bot_ids.map((id) => (
                    <span key={id} className="rounded-md bg-violet-400/10 px-2 py-0.5 text-[11px] text-violet-200">
                      {botName(id)}
                    </span>
                  ))
                )}
              </div>

              {p.rationale && (
                <p className="mt-3 text-sm leading-relaxed text-gray-300">{p.rationale}</p>
              )}
              {p.expected_synergy && (
                <p className="mt-1.5 text-xs text-gray-500">시너지: {p.expected_synergy}</p>
              )}

              {/* 봇 투표 */}
              {p.votes.length > 0 && (
                <div className="mt-4 space-y-2 border-t border-white/[0.06] pt-3">
                  <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
                    봇 의견
                  </p>
                  {p.votes.map((v) => (
                    <div key={v.bot_id} className="rounded-lg bg-black/20 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-white">{v.bot_name}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${STANCE[v.stance].cls}`}>
                          {STANCE[v.stance].label}
                        </span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-gray-400">
                        {v.comment}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {/* 액션 */}
              {p.status === "proposed" ? (
                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-3">
                  <button
                    onClick={() => poll(p.id)}
                    disabled={busyId === p.id || p.member_bot_ids.length === 0}
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-200 transition hover:bg-white/10 disabled:opacity-40"
                  >
                    {busyId === p.id ? "처리 중…" : "봇 의견 수집"}
                  </button>
                  <div className="flex-1" />
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-gray-400">
                    <input
                      type="checkbox"
                      checked={autoRun}
                      onChange={(e) => setAutoRun(e.target.checked)}
                      className="accent-violet-500"
                    />
                    승인 즉시 실행
                  </label>
                  <button
                    onClick={() => decide(p.id, "reject")}
                    disabled={busyId === p.id}
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-400 transition hover:border-rose-500/40 hover:text-rose-300 disabled:opacity-40"
                  >
                    거부
                  </button>
                  <button
                    onClick={() => decide(p.id, "approve")}
                    disabled={busyId === p.id || p.member_bot_ids.length === 0}
                    className="rounded-lg bg-gradient-to-r from-emerald-500 to-teal-500 px-4 py-1.5 text-xs font-semibold text-white shadow-lg shadow-emerald-500/25 transition hover:brightness-110 disabled:opacity-40"
                  >
                    {busyId === p.id && autoRun ? "승인·실행 중…" : "✓ 승인 (사람 결정)"}
                  </button>
                </div>
              ) : p.status === "approved" && p.created_squad_id ? (
                <ApprovedBox
                  p={p}
                  busy={busyId === p.id}
                  onRun={(input) => runApproved(p.id, input)}
                />
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function ApprovedBox({
  p,
  busy,
  onRun,
}: {
  p: Proposal;
  busy: boolean;
  onRun: (input: string) => void;
}) {
  const [input, setInput] = useState(p.run_input || p.goal);
  return (
    <div className="mt-4 space-y-3 border-t border-white/[0.06] pt-3">
      <div className="text-xs text-emerald-300">
        ✓ 승인됨 → 스쿼드 <code className="text-emerald-200">{p.created_squad_id}</code> 생성됨.
      </div>
      <div className="flex items-end gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={2}
          placeholder="실행 입력 (비우면 목표 사용)"
          className="flex-1 resize-none rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20"
        />
        <button
          onClick={() => onRun(input)}
          disabled={busy}
          className="h-[40px] shrink-0 rounded-lg bg-gradient-to-r from-violet-500 to-indigo-500 px-4 text-xs font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-40"
        >
          {busy ? "실행 중…" : p.run_turns.length > 0 ? "재실행" : "스쿼드 실행"}
        </button>
      </div>

      {p.run_status === "running" && (
        <p className="text-xs text-gray-500">봇들을 실행 중입니다…</p>
      )}

      {p.run_turns.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
            실행 결과 {p.run_status === "error" && "(일부 실패)"}
          </p>
          {p.run_turns.map((t, i) => (
            <div key={i} className="rounded-lg bg-black/20 px-3 py-2">
              <div className="mb-1 flex items-center gap-2">
                <span>🤖</span>
                <span className="text-sm font-medium text-white">{t.bot_name}</span>
                <span className="text-[11px] text-gray-500">{t.role}</span>
              </div>
              <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-gray-300">
                {t.ok ? t.output : `[오류] ${t.error}`}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
