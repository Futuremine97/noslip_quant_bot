"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

type AgentKind = "claude" | "codex" | "antigravity" | "custom";
type Status = "unknown" | "ok" | "error";

interface ChatAgent {
  id: string;
  name: string;
  kind: AgentKind;
  command: string;
  args: string[];
  prompt_mode: "arg" | "stdin";
  enabled: boolean;
  last_status: Status;
}

interface Msg {
  role: "user" | "assistant";
  content: string;
  meta?: string;
  error?: boolean;
}

const KIND_ICON: Record<AgentKind, string> = {
  claude: "🟣",
  codex: "🟢",
  antigravity: "🔵",
  custom: "⚙️",
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

export default function ChatPage() {
  const [agents, setAgents] = useState<ChatAgent[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadAgents = useCallback(async () => {
    try {
      const list = await api<ChatAgent[]>("/api/chat/agents");
      setAgents(list);
      setActiveId((cur) => cur || list.find((a) => a.enabled)?.id || "");
    } catch (e) {
      setErr(
        e instanceof Error
          ? `${e.message} — 백엔드(:8787)가 실행 중인지 확인하세요.`
          : String(e),
      );
    }
  }, []);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [msgs, sending]);

  const active = agents.find((a) => a.id === activeId);

  // 마지막 assistant 메시지를 패치
  const patchLast = (patch: Partial<Msg>) =>
    setMsgs((m) => {
      const c = [...m];
      const i = c.length - 1;
      if (i >= 0 && c[i].role === "assistant") c[i] = { ...c[i], ...patch };
      return c;
    });

  const send = async () => {
    const text = input.trim();
    if (!text || !active || sending) return;
    setErr(null);
    const history = msgs.map((m) => ({ role: m.role, content: m.content }));
    setMsgs((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "", meta: active.name },
    ]);
    setInput("");
    setSending(true);

    try {
      const res = await fetch(`${API}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: active.id, message: text, history }),
      });
      if (!res.ok || !res.body) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `요청 실패 (${res.status})`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let acc = "";
      const started = Date.now();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const part of parts) {
          const dataLine = part.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          let ev: { type: string; text?: string; error?: string; ok?: boolean };
          try {
            ev = JSON.parse(dataLine.slice(5).trim());
          } catch {
            continue;
          }
          if (ev.type === "chunk") {
            acc += ev.text || "";
            patchLast({ content: acc });
          } else if (ev.type === "error") {
            patchLast({
              content: (acc ? acc + "\n\n" : "") + `[오류] ${ev.error}`,
              error: true,
            });
          } else if (ev.type === "done") {
            if (!ev.ok && !acc) {
              patchLast({ content: `[오류] ${ev.error}`, error: true });
            } else {
              patchLast({
                content: acc || "(빈 응답)",
                meta: `${active.name} · ${((Date.now() - started) / 1000).toFixed(1)}s`,
              });
            }
          }
        }
      }
    } catch (e) {
      patchLast({
        content: e instanceof Error ? e.message : String(e),
        error: true,
      });
    } finally {
      setSending(false);
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <main className="flex h-screen flex-col">
      {/* 헤더 */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-8 py-4">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-white">채팅</h1>
          {agents.length > 0 && (
            <select
              value={activeId}
              onChange={(e) => setActiveId(e.target.value)}
              className="rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-sm text-white outline-none focus:border-violet-400/60"
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id} disabled={!a.enabled}>
                  {KIND_ICON[a.kind]} {a.name}
                  {!a.enabled ? " (비활성)" : ""}
                </option>
              ))}
            </select>
          )}
          {active && (
            <span
              className={`h-2 w-2 rounded-full ${
                active.last_status === "ok"
                  ? "bg-emerald-400"
                  : active.last_status === "error"
                    ? "bg-rose-400"
                    : "bg-gray-500"
              }`}
              title={`상태: ${active.last_status}`}
            />
          )}
        </div>
        <div className="flex gap-2">
          {msgs.length > 0 && (
            <button
              onClick={() => setMsgs([])}
              className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-gray-400 transition hover:bg-white/5"
            >
              대화 비우기
            </button>
          )}
          <button
            onClick={() => setPanelOpen(true)}
            className="rounded-lg bg-gradient-to-r from-violet-500 to-indigo-500 px-3 py-1.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110"
          >
            + 에이전트 연결
          </button>
        </div>
      </header>

      {err && (
        <div className="mx-8 mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          ⚠️ {err}
        </div>
      )}

      {/* 메시지 영역 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          {agents.length === 0 ? (
            <EmptyAgents onConnect={() => setPanelOpen(true)} />
          ) : msgs.length === 0 ? (
            <div className="pt-20 text-center text-sm text-gray-500">
              <div className="mb-3 text-4xl">{active ? KIND_ICON[active.kind] : "💬"}</div>
              {active ? `${active.name} 와 대화를 시작하세요.` : "에이전트를 선택하세요."}
            </div>
          ) : (
            msgs.map((m, i) => <Bubble key={i} m={m} />)
          )}
          {sending && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <span className="flex gap-1">
                <Dot /> <Dot d={150} /> <Dot d={300} />
              </span>
              실행 중…
            </div>
          )}
        </div>
      </div>

      {/* 입력 */}
      <div className="shrink-0 border-t border-white/[0.06] px-8 py-4">
        <div className="mx-auto flex max-w-3xl items-end gap-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            rows={1}
            disabled={!active || sending}
            placeholder={
              active ? `${active.name} 에게 메시지… (Enter 전송, Shift+Enter 줄바꿈)` : "에이전트를 연결하세요"
            }
            className="max-h-40 min-h-[44px] flex-1 resize-none rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20 disabled:opacity-50"
          />
          <button
            onClick={send}
            disabled={!active || sending || !input.trim()}
            className="h-[44px] rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-40"
          >
            전송
          </button>
        </div>
      </div>

      {panelOpen && (
        <ConnectPanel
          onClose={() => setPanelOpen(false)}
          onDone={async () => {
            setPanelOpen(false);
            await loadAgents();
          }}
        />
      )}
    </main>
  );
}

function Bubble({ m }: { m: Msg }) {
  const isUser = m.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "bg-gradient-to-br from-violet-500 to-indigo-500 text-white"
            : m.error
              ? "border border-rose-500/30 bg-rose-500/10 text-rose-200"
              : "border border-white/[0.07] bg-white/[0.04] text-gray-200"
        }`}
      >
        <pre className="whitespace-pre-wrap break-words font-sans">{m.content}</pre>
        {m.meta && <div className="mt-1.5 text-[10px] text-white/50">{m.meta}</div>}
      </div>
    </div>
  );
}

function Dot({ d = 0 }: { d?: number }) {
  return (
    <span
      className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-500"
      style={{ animationDelay: `${d}ms` }}
    />
  );
}

function EmptyAgents({ onConnect }: { onConnect: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-white/[0.02] py-20 text-center">
      <div className="mb-4 text-3xl">💬</div>
      <h3 className="text-base font-semibold text-white">연결된 에이전트가 없습니다</h3>
      <p className="mt-1 max-w-sm text-sm text-gray-500">
        Claude · Codex · Antigravity CLI를 연결하면 여기서 바로 대화할 수 있습니다.
      </p>
      <button
        onClick={onConnect}
        className="mt-5 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110"
      >
        + 에이전트 연결
      </button>
    </div>
  );
}

/* ───────────────────── 에이전트 연결 슬라이드오버 ───────────────────── */
const PRESETS: Record<
  AgentKind,
  { label: string; command: string; args: string; prompt_mode: "arg" | "stdin" }
> = {
  claude: { label: "Claude Code", command: "claude", args: "-p", prompt_mode: "arg" },
  codex: { label: "Codex", command: "codex", args: "exec", prompt_mode: "arg" },
  antigravity: { label: "Antigravity", command: "antigravity", args: "", prompt_mode: "arg" },
  custom: { label: "직접 입력", command: "", args: "", prompt_mode: "arg" },
};

function ConnectPanel({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: () => void;
}) {
  const [kind, setKind] = useState<AgentKind>("claude");
  const [name, setName] = useState("Claude Code");
  const [command, setCommand] = useState(PRESETS.claude.command);
  const [args, setArgs] = useState(PRESETS.claude.args);
  const [promptMode, setPromptMode] = useState<"arg" | "stdin">("arg");
  const [local, setLocal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pick = (k: AgentKind) => {
    setKind(k);
    const p = PRESETS[k];
    setName(p.label);
    setCommand(p.command);
    setArgs(p.args);
    setPromptMode(p.prompt_mode);
  };

  const field =
    "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-500/20";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/chat/agents", {
        method: "POST",
        body: JSON.stringify({
          name,
          kind,
          command,
          args: args.split(" ").map((a) => a.trim()).filter(Boolean),
          prompt_mode: promptMode,
          local,
        }),
      });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <form
        onSubmit={submit}
        className="relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-white/10 bg-[#0a0b16] p-7 shadow-2xl"
      >
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">에이전트 연결</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 transition hover:bg-white/10 hover:text-white"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        <div className="mb-5 grid grid-cols-2 gap-2">
          {(Object.keys(PRESETS) as AgentKind[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => pick(k)}
              className={`flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm transition ${
                kind === k
                  ? "border-violet-400/60 bg-violet-500/15 text-white"
                  : "border-white/10 text-gray-400 hover:bg-white/5"
              }`}
            >
              <span>{KIND_ICON[k]}</span>
              {PRESETS[k].label}
            </button>
          ))}
        </div>

        <div className="space-y-4">
          <Labeled label="이름">
            <input required value={name} onChange={(e) => setName(e.target.value)} className={field} />
          </Labeled>
          <Labeled label="command (실행 파일)">
            <input
              required
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              className={field}
              placeholder="claude"
            />
          </Labeled>
          <Labeled label="args (공백 구분)">
            <input value={args} onChange={(e) => setArgs(e.target.value)} className={field} placeholder="-p" />
          </Labeled>
          <Labeled label="프롬프트 전달">
            <div className="grid grid-cols-2 gap-2">
              {(["arg", "stdin"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setPromptMode(m)}
                  className={`rounded-lg border px-3 py-2 text-sm transition ${
                    promptMode === m
                      ? "border-violet-400/60 bg-violet-500/15 text-white"
                      : "border-white/10 text-gray-400 hover:bg-white/5"
                  }`}
                >
                  {m === "arg" ? "마지막 인자" : "표준입력(stdin)"}
                </button>
              ))}
            </div>
          </Labeled>
          <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-white/10 bg-black/20 px-3 py-2.5">
            <input
              type="checkbox"
              checked={local}
              onChange={(e) => setLocal(e.target.checked)}
              className="accent-violet-500"
            />
            <span className="text-sm text-gray-300">
              로컬 모델 (동반 기능에서 우선 선택)
            </span>
          </label>
          <p className="rounded-lg bg-white/[0.03] px-3 py-2 text-xs leading-relaxed text-gray-500">
            로컬에 해당 CLI가 설치되어 있고 로그인된 상태여야 합니다. 등록 후
            카드의 &lsquo;연결 점검&rsquo;으로 실행 가능 여부를 확인하세요.
          </p>
          {err && (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {err}
            </div>
          )}
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
            disabled={busy}
            className="flex-1 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:brightness-110 disabled:opacity-50"
          >
            {busy ? "연결 중…" : "연결"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-gray-400">{label}</span>
      {children}
    </label>
  );
}
