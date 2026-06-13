"use client";

import { useCallback, useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

interface Config {
  nsq_per_unit: number;
  initial_grant: number;
  actions: Record<string, number>;
}
interface Entry {
  ts: string;
  type: "grant" | "charge";
  action: string;
  units: number;
  nsq: number;
  note?: string;
}
interface Account {
  id: string;
  balance: number;
  granted: number;
  spent: number;
  entries: Entry[];
}
interface Usage {
  by_action: { action: string; count: number; units: number; nsq: number }[];
}

const ACTION_LABEL: Record<string, string> = {
  agent_run: "에이전트 실행",
  purpose_plan: "Purpose 전략",
  squad_run_per_bot: "스쿼드(봇당)",
  federation_propose: "연합 역제안",
  federation_run_per_bot: "연합 실행(봇당)",
  companion_nudge: "동반 역질문",
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

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 4 });

export default function TokenPage() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [acct, setAcct] = useState<Account | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [grantAmt, setGrantAmt] = useState("100");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [c, a, u] = await Promise.all([
      api<Config>("/api/token/config"),
      api<Account>("/api/token/account?limit=40"),
      api<Usage>("/api/token/usage"),
    ]);
    setCfg(c);
    setAcct(a);
    setUsage(u);
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [c, a, u] = await Promise.all([
          api<Config>("/api/token/config"),
          api<Account>("/api/token/account?limit=40"),
          api<Usage>("/api/token/usage"),
        ]);
        if (!alive) return;
        setCfg(c);
        setAcct(a);
        setUsage(u);
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

  const grant = async () => {
    const amt = parseFloat(grantAmt);
    if (!(amt > 0)) return;
    setBusy(true);
    setErr(null);
    try {
      await api("/api/token/grant", { method: "POST", body: JSON.stringify({ amount: amt }) });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const patchAction = async (action: string, units: number) => {
    setErr(null);
    try {
      const c = await api<Config>("/api/token/config", {
        method: "PUT",
        body: JSON.stringify({ actions: { [action]: units } }),
      });
      setCfg(c);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const patchRate = async (nsq_per_unit: number) => {
    try {
      setCfg(await api<Config>("/api/token/config", {
        method: "PUT",
        body: JSON.stringify({ nsq_per_unit }),
      }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const maxNsq = Math.max(1, ...(usage?.by_action.map((x) => x.nsq) || [1]));

  return (
    <main className="mx-auto w-full max-w-5xl px-8 py-12">
      <header className="mb-8">
        <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
          통합 관리 / 토큰화
        </p>
        <h1 className="mt-1.5 text-[28px] font-bold tracking-tight text-white">
          사용량 토큰화 (NSQ)
        </h1>
        <p className="mt-1.5 text-sm text-gray-400">
          AI 액션 사용량을 NSQ 토큰 단위로 환산·차감합니다. 온체인 결제(Solana)의 정산 전 단계입니다.
        </p>
      </header>

      {err && (
        <div className="mb-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          ⚠️ {err}
        </div>
      )}

      {!acct || !cfg ? (
        <p className="text-sm text-gray-500">불러오는 중…</p>
      ) : (
        <>
          {/* 잔액 카드 */}
          <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-2xl border border-violet-400/30 bg-gradient-to-br from-violet-500/15 to-indigo-500/5 p-5">
              <div className="text-xs text-gray-400">잔액</div>
              <div className={`mt-1 text-3xl font-bold ${acct.balance < 0 ? "text-rose-300" : "text-white"}`}>
                {fmt(acct.balance)} <span className="text-base text-gray-400">NSQ</span>
              </div>
            </div>
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
              <div className="text-xs text-gray-400">누적 사용</div>
              <div className="mt-1 text-3xl font-bold text-white">{fmt(acct.spent)}</div>
            </div>
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
              <div className="text-xs text-gray-400">누적 지급</div>
              <div className="mt-1 text-3xl font-bold text-white">{fmt(acct.granted)}</div>
            </div>
          </div>

          {/* 지급 + 단가 */}
          <div className="mb-8 flex flex-wrap items-end gap-3 rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5">
            <label className="block">
              <span className="mb-1.5 block text-xs text-gray-400">NSQ 지급(충전)</span>
              <input
                value={grantAmt}
                onChange={(e) => setGrantAmt(e.target.value)}
                inputMode="decimal"
                className="w-32 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-violet-400/60"
              />
            </label>
            <button
              onClick={grant}
              disabled={busy}
              className="rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-40"
            >
              지급
            </button>
            <label className="ml-auto block">
              <span className="mb-1.5 block text-xs text-gray-400">단가 (NSQ / unit)</span>
              <input
                defaultValue={cfg.nsq_per_unit}
                onBlur={(e) => patchRate(parseFloat(e.target.value) || 0)}
                inputMode="decimal"
                className="w-28 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-violet-400/60"
              />
            </label>
          </div>

          <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            {/* 요금표 */}
            <section>
              <h2 className="mb-4 text-lg font-semibold text-white">요금표 (units / 액션)</h2>
              <ul className="space-y-2">
                {Object.entries(cfg.actions).map(([action, units]) => (
                  <li
                    key={action}
                    className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.03] px-4 py-2.5"
                  >
                    <span className="text-sm text-gray-200">{ACTION_LABEL[action] || action}</span>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min={0}
                        defaultValue={units}
                        onBlur={(e) => patchAction(action, parseInt(e.target.value, 10) || 0)}
                        className="w-16 rounded-lg border border-white/10 bg-black/30 px-2 py-1 text-right text-sm text-white outline-none focus:border-violet-400/60"
                      />
                      <span className="w-20 text-right text-xs text-gray-500">
                        = {fmt(units * cfg.nsq_per_unit)} NSQ
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            {/* 사용량 시각화 */}
            <section>
              <h2 className="mb-4 text-lg font-semibold text-white">액션별 사용량 (NSQ)</h2>
              {!usage || usage.by_action.length === 0 ? (
                <p className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] px-4 py-8 text-center text-sm text-gray-500">
                  아직 과금된 사용량이 없습니다.
                </p>
              ) : (
                <ul className="space-y-3">
                  {usage.by_action.map((u) => (
                    <li key={u.action}>
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span className="text-gray-300">{ACTION_LABEL[u.action] || u.action}</span>
                        <span className="text-gray-500">{u.count}회 · {fmt(u.nsq)} NSQ</span>
                      </div>
                      <div className="h-2.5 overflow-hidden rounded-full bg-white/[0.06]">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-500"
                          style={{ width: `${Math.max(4, (u.nsq / maxNsq) * 100)}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          {/* 원장 */}
          <section className="mt-10">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">원장 (최근 40건)</h2>
              <button
                onClick={load}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/5"
              >
                새로고침
              </button>
            </div>
            <ul className="divide-y divide-white/[0.05] rounded-xl border border-white/[0.07] bg-white/[0.02]">
              {acct.entries.map((e, i) => (
                <li key={i} className="flex items-center justify-between px-4 py-2.5 text-sm">
                  <div className="flex items-center gap-2.5">
                    <span className={e.type === "grant" ? "text-emerald-400" : "text-gray-500"}>
                      {e.type === "grant" ? "＋" : "－"}
                    </span>
                    <span className="text-gray-300">
                      {e.type === "grant" ? e.note || "지급" : ACTION_LABEL[e.action] || e.action}
                    </span>
                    <span className="text-[11px] text-gray-600">{new Date(e.ts).toLocaleString()}</span>
                  </div>
                  <span className={e.type === "grant" ? "font-medium text-emerald-300" : "text-gray-400"}>
                    {e.type === "grant" ? "+" : "−"}{fmt(e.nsq)} NSQ
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </main>
  );
}
