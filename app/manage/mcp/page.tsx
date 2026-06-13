"use client";

import { useCallback, useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

type Transport = "stdio" | "sse" | "http";
type Status = "unknown" | "ok" | "error";

interface MCPServer {
  id: string;
  name: string;
  transport: Transport;
  command?: string | null;
  args: string[];
  url?: string | null;
  env: Record<string, string>;
  enabled: boolean;
  tags: string[];
  last_status: Status;
  last_checked_at?: string | null;
}

const STATUS_DOT: Record<Status, string> = {
  ok: "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/60",
  error: "bg-rose-400 shadow-[0_0_8px] shadow-rose-400/60",
  unknown: "bg-gray-500",
};
const STATUS_LABEL: Record<Status, string> = {
  ok: "정상",
  error: "오류",
  unknown: "미확인",
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

const EMPTY_FORM = {
  name: "",
  transport: "stdio" as Transport,
  command: "",
  args: "",
  url: "",
  env: "",
};

export default function MCPManagePage() {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setServers(await api<MCPServer[]>("/api/mcp/servers"));
    } catch (e) {
      setErr(
        e instanceof Error
          ? `${e.message} — 백엔드(:8787)가 실행 중인지 확인하세요.`
          : String(e),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setErr(null);
    try {
      const env: Record<string, string> = {};
      form.env
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .forEach((line) => {
          const idx = line.indexOf("=");
          if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        });
      await api("/api/mcp/servers", {
        method: "POST",
        body: JSON.stringify({
          name: form.name,
          transport: form.transport,
          command: form.transport === "stdio" ? form.command : undefined,
          args:
            form.transport === "stdio"
              ? form.args.split(" ").map((a) => a.trim()).filter(Boolean)
              : [],
          url: form.transport !== "stdio" ? form.url : undefined,
          env,
        }),
      });
      setForm(EMPTY_FORM);
      setPanelOpen(false);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const check = async (id: string) => {
    setChecking(id);
    try {
      await api(`/api/mcp/servers/${id}/check`, { method: "POST" });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(null);
    }
  };

  const remove = async (id: string) => {
    if (!confirm(`'${id}' 서버를 삭제할까요?`)) return;
    try {
      await api(`/api/mcp/servers/${id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-12">
      {/* 헤더 */}
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-widest text-gray-500">
            통합 관리 / MCP
          </p>
          <h1 className="mt-1.5 text-[28px] font-bold tracking-tight text-white">
            MCP 서버
          </h1>
          <p className="mt-1.5 text-sm text-gray-400">
            MCP 서버를 등록하고 연결 상태를 점검합니다.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            className="rounded-xl border border-white/10 px-4 py-2.5 text-sm text-gray-300 transition hover:bg-white/[0.05]"
          >
            새로고침
          </button>
          <button
            onClick={() => {
              setForm(EMPTY_FORM);
              setPanelOpen(true);
            }}
            className="rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110"
          >
            + 서버 추가
          </button>
        </div>
      </header>

      {err && (
        <div className="mb-6 flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          <span>⚠️</span>
          <span>{err}</span>
        </div>
      )}

      {/* 목록 */}
      {loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-36 animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.03]"
            />
          ))}
        </div>
      ) : servers.length === 0 ? (
        <EmptyState onAdd={() => setPanelOpen(true)} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {servers.map((s) => (
            <ServerCard
              key={s.id}
              s={s}
              checking={checking === s.id}
              onCheck={() => check(s.id)}
              onRemove={() => remove(s.id)}
            />
          ))}
        </div>
      )}

      {/* 슬라이드오버 등록 패널 */}
      {panelOpen && (
        <AddPanel
          form={form}
          setForm={setForm}
          submitting={submitting}
          onSubmit={submit}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </main>
  );
}

/* ───────────────────────── 서버 카드 ───────────────────────── */
function ServerCard({
  s,
  checking,
  onCheck,
  onRemove,
}: {
  s: MCPServer;
  checking: boolean;
  onCheck: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="group flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.03] p-5 transition hover:border-white/15 hover:bg-white/[0.05]">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-white/10 to-white/5 text-base">
            {s.transport === "stdio" ? "⌨️" : "🌐"}
          </div>
          <div className="min-w-0">
            <div className="truncate font-semibold text-white">{s.name}</div>
            <div className="text-[11px] text-gray-500">{s.id}</div>
          </div>
        </div>
        <span
          className="flex items-center gap-1.5 rounded-full bg-white/5 px-2 py-1 text-[10px] text-gray-300"
          title={STATUS_LABEL[s.last_status]}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[s.last_status]}`} />
          {STATUS_LABEL[s.last_status]}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-[10px] font-medium text-gray-300">
          {s.transport}
        </span>
        {!s.enabled && (
          <span className="rounded-md bg-gray-500/20 px-2 py-0.5 text-[10px] text-gray-400">
            비활성
          </span>
        )}
        {s.tags.map((t) => (
          <span
            key={t}
            className="rounded-md bg-violet-400/10 px-2 py-0.5 text-[10px] text-violet-300"
          >
            {t}
          </span>
        ))}
      </div>

      <p className="mt-3 truncate rounded-lg bg-black/30 px-2.5 py-1.5 font-mono text-[11px] text-gray-500">
        {s.transport === "stdio"
          ? `${s.command ?? ""} ${s.args.join(" ")}`.trim() || "—"
          : s.url || "—"}
      </p>

      <div className="mt-4 flex gap-2 border-t border-white/[0.06] pt-3">
        <button
          onClick={onCheck}
          disabled={checking}
          className="flex-1 rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-gray-200 transition hover:bg-white/10 disabled:opacity-50"
        >
          {checking ? "점검 중…" : "연결 점검"}
        </button>
        <button
          onClick={onRemove}
          className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-400 transition hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-300"
        >
          삭제
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────── 빈 상태 ───────────────────────── */
function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-white/[0.02] py-20 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-white/[0.05] text-2xl">
        🧩
      </div>
      <h3 className="text-base font-semibold text-white">
        등록된 MCP 서버가 없습니다
      </h3>
      <p className="mt-1 max-w-sm text-sm text-gray-500">
        첫 MCP 서버를 추가하거나, 루트 <code>.mcp.json</code>이 자동
        임포트되도록 백엔드를 재시작하세요.
      </p>
      <button
        onClick={onAdd}
        className="mt-5 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110"
      >
        + 서버 추가
      </button>
    </div>
  );
}

/* ───────────────────────── 등록 슬라이드오버 ───────────────────────── */
function AddPanel({
  form,
  setForm,
  submitting,
  onSubmit,
  onClose,
}: {
  form: typeof EMPTY_FORM;
  setForm: (f: typeof EMPTY_FORM) => void;
  submitting: boolean;
  onSubmit: (e: React.FormEvent) => void;
  onClose: () => void;
}) {
  const field =
    "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20";
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <form
        onSubmit={onSubmit}
        className="relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-white/10 bg-[#0a0b16] p-7 shadow-2xl"
      >
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">새 MCP 서버</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 transition hover:bg-white/10 hover:text-white"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        <div className="space-y-4">
          <Labeled label="이름">
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={field}
              placeholder="예: My Notion MCP"
            />
          </Labeled>

          <Labeled label="트랜스포트">
            <div className="grid grid-cols-3 gap-2">
              {(["stdio", "sse", "http"] as Transport[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setForm({ ...form, transport: t })}
                  className={`rounded-lg border px-3 py-2 text-sm transition ${
                    form.transport === t
                      ? "border-violet-400/60 bg-violet-500/15 text-white"
                      : "border-white/10 text-gray-400 hover:bg-white/5"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </Labeled>

          {form.transport === "stdio" ? (
            <>
              <Labeled label="command">
                <input
                  value={form.command}
                  onChange={(e) => setForm({ ...form, command: e.target.value })}
                  className={field}
                  placeholder="npx"
                />
              </Labeled>
              <Labeled label="args (공백 구분)">
                <input
                  value={form.args}
                  onChange={(e) => setForm({ ...form, args: e.target.value })}
                  className={field}
                  placeholder="-y @scope/server"
                />
              </Labeled>
            </>
          ) : (
            <Labeled label="URL">
              <input
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                className={field}
                placeholder="https://example.com/mcp"
              />
            </Labeled>
          )}

          <Labeled label="환경변수 (KEY=value, 시크릿은 secret://이름)">
            <textarea
              value={form.env}
              onChange={(e) => setForm({ ...form, env: e.target.value })}
              rows={3}
              className={`${field} font-mono text-xs`}
              placeholder={"API_TOKEN=secret://my_token"}
            />
          </Labeled>
        </div>

        <div className="mt-auto flex gap-2 pt-8">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-xl border border-white/10 px-4 py-2.5 text-sm text-gray-300 transition hover:bg-white/5"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="flex-1 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-50"
          >
            {submitting ? "등록 중…" : "등록"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Labeled({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-gray-400">
        {label}
      </span>
      {children}
    </label>
  );
}
