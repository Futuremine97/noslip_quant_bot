"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://127.0.0.1:8787";

interface Stat {
  total: number;
  ok: number;
  error: number;
}

export default function ManageHome() {
  const [stat, setStat] = useState<Stat | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${API}/api/mcp/servers`)
      .then((r) => r.json())
      .then((b) => {
        const list = (b.data ?? []) as { last_status: string }[];
        setStat({
          total: list.length,
          ok: list.filter((s) => s.last_status === "ok").length,
          error: list.filter((s) => s.last_status === "error").length,
        });
        setReachable(true);
      })
      .catch(() => setReachable(false));
  }, []);

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-12">
      <header className="mb-10">
        <h1 className="text-[28px] font-bold tracking-tight text-white">
          통합 관리
        </h1>
        <p className="mt-1.5 text-sm text-gray-400">
          Connector · MCP · Skill을 한 곳에서 등록하고 연결 상태를 관리합니다.
        </p>
      </header>

      {/* 상태 요약 */}
      <div className="mb-10 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="등록된 MCP"
          value={stat ? String(stat.total) : "—"}
          accent="from-violet-500/20 to-indigo-500/10"
        />
        <StatCard
          label="정상"
          value={stat ? String(stat.ok) : "—"}
          accent="from-emerald-500/20 to-emerald-500/5"
          dot="bg-emerald-400"
        />
        <StatCard
          label="오류"
          value={stat ? String(stat.error) : "—"}
          accent="from-rose-500/20 to-rose-500/5"
          dot="bg-rose-400"
        />
        <StatCard
          label="백엔드"
          value={reachable === null ? "…" : reachable ? "연결됨" : "끊김"}
          accent="from-sky-500/20 to-sky-500/5"
          dot={reachable ? "bg-emerald-400" : "bg-gray-500"}
        />
      </div>

      {/* 리소스 카드 */}
      <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-gray-500">
        관리 대상
      </h2>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
        <ResourceCard
          href="/manage/mcp"
          title="MCP 서버"
          desc="MCP 서버 등록 · 연결 점검 · 도구 목록"
          emoji="🧩"
          available
        />
        <ResourceCard
          href="#"
          title="커넥터"
          desc="Google Drive · Notion 등 데이터 소스 연결"
          emoji="🔌"
        />
        <ResourceCard
          href="#"
          title="Skill"
          desc="Skill 업로드 · 활성화 · 실행 관리"
          emoji="🛠️"
        />
      </div>
    </main>
  );
}

function StatCard({
  label,
  value,
  accent,
  dot,
}: {
  label: string;
  value: string;
  accent: string;
  dot?: string;
}) {
  return (
    <div
      className={`relative overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-br ${accent} p-5`}
    >
      <div className="flex items-center gap-2">
        {dot && <span className={`h-2 w-2 rounded-full ${dot}`} />}
        <span className="text-xs text-gray-400">{label}</span>
      </div>
      <div className="mt-2 text-3xl font-bold text-white">{value}</div>
    </div>
  );
}

function ResourceCard({
  href,
  title,
  desc,
  emoji,
  available,
}: {
  href: string;
  title: string;
  desc: string;
  emoji: string;
  available?: boolean;
}) {
  const inner = (
    <div
      className={`group h-full rounded-2xl border border-white/[0.07] bg-white/[0.03] p-6 transition ${
        available
          ? "hover:-translate-y-0.5 hover:border-violet-400/40 hover:bg-white/[0.06] hover:shadow-xl hover:shadow-violet-500/5"
          : "opacity-50"
      }`}
    >
      <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-white/[0.06] text-xl">
        {emoji}
      </div>
      <div className="flex items-center gap-2">
        <h3 className="font-semibold text-white">{title}</h3>
        {!available && (
          <span className="rounded-md bg-amber-400/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
            예정
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm leading-relaxed text-gray-400">{desc}</p>
      {available && (
        <div className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-violet-300">
          관리하기
          <span className="transition group-hover:translate-x-0.5">→</span>
        </div>
      )}
    </div>
  );
  return available ? (
    <Link href={href}>{inner}</Link>
  ) : (
    <div className="cursor-not-allowed">{inner}</div>
  );
}
