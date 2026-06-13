"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/manage", label: "개요", icon: GridIcon, exact: true },
  { href: "/manage/purpose", label: "Purpose", icon: TargetIcon },
  { href: "/manage/bots", label: "멀티봇", icon: BotsIcon },
  { href: "/manage/federation", label: "연합", icon: FederationIcon },
  { href: "/manage/token", label: "토큰화", icon: TokenIcon },
  { href: "/manage/chat", label: "채팅", icon: ChatIcon },
  { href: "/manage/companion", label: "동반", icon: CompanionIcon },
  { href: "/manage/mcp", label: "MCP 서버", icon: PuzzleIcon },
  { href: "/manage/connectors", label: "커넥터", icon: PlugIcon, soon: true },
  { href: "/manage/skills", label: "Skill", icon: SparkIcon, soon: true },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col border-r border-white/[0.06] bg-[#0a0b16]/80 px-4 py-6 backdrop-blur-xl">
      <Link href="/manage" className="mb-8 flex items-center gap-2.5 px-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 text-sm font-bold text-white shadow-lg shadow-violet-500/20">
          N
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-white">Control Plane</div>
          <div className="text-[10px] tracking-wide text-gray-500">
            NoSlip 통합 관리
          </div>
        </div>
      </Link>

      <nav className="flex flex-1 flex-col gap-1">
        {NAV.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.soon ? "#" : item.href}
              aria-disabled={item.soon}
              className={`group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
                active
                  ? "bg-white/[0.08] text-white"
                  : item.soon
                    ? "cursor-not-allowed text-gray-600"
                    : "text-gray-400 hover:bg-white/[0.04] hover:text-gray-200"
              }`}
            >
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-gradient-to-b from-violet-400 to-indigo-400" />
              )}
              <Icon className="h-[18px] w-[18px]" />
              <span className="flex-1">{item.label}</span>
              {item.soon && (
                <span className="rounded-md bg-white/5 px-1.5 py-0.5 text-[9px] font-medium text-gray-500">
                  예정
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <p className="text-[11px] leading-relaxed text-gray-500">
          API 백엔드는{" "}
          <code className="text-gray-400">:8787</code> 에서 실행됩니다.
        </p>
      </div>
    </aside>
  );
}

/* ── 아이콘 (의존성 없이 인라인 SVG) ── */
type IconProps = { className?: string };

function GridIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function PuzzleIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19.4 13a2 2 0 0 0 0-4h-1a2 2 0 0 1-2-2.5 2 2 0 0 0-4 0A2 2 0 0 1 10 9H9a2 2 0 0 0 0 4h1a2 2 0 0 1 2 2.5 2 2 0 0 0 4 0 2 2 0 0 1 2.4-2.5Z" />
    </svg>
  );
}
function PlugIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0V8ZM12 16v6" />
    </svg>
  );
}
function TokenIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 0 1 2.5-1.5c1.4 0 2.5.9 2.5 2s-1 1.6-2.5 2-2.5 1-2.5 2 1.1 2 2.5 2a2.5 2.5 0 0 0 2.5-1.5M12 6.5v1.5M12 16v1.5" />
    </svg>
  );
}
function FederationIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M7.7 7.7 10.3 16M16.3 7.7 13.7 16M8 6h8" />
    </svg>
  );
}
function TargetIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.2" />
    </svg>
  );
}
function BotsIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="8" width="16" height="11" rx="2.5" />
      <path d="M12 8V4M9 13h.01M15 13h.01M2 12v3M22 12v3" />
    </svg>
  );
}
function ChatIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-9 8.4 9 9 0 0 1-4-1L3 20l1.1-4A8.38 8.38 0 0 1 3 11.5 8.5 8.5 0 0 1 21 11.5Z" />
    </svg>
  );
}
function CompanionIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9.5 9h.01M14.5 9h.01M9 14s1 1.5 3 1.5 3-1.5 3-1.5" />
      <path d="M12 2a7 7 0 0 0-7 7v3l-1.5 3A1 1 0 0 0 4.4 16H6v2a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-2h1.6a1 1 0 0 0 .9-1.5L19 12V9a7 7 0 0 0-7-7Z" />
    </svg>
  );
}
function SparkIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
    </svg>
  );
}
