import type { ReactNode } from "react";
import Sidebar from "./Sidebar";

export default function ManageLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-screen w-full bg-[#06070f] text-gray-200">
      {/* 배경 글로우 */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 opacity-60"
        style={{
          background:
            "radial-gradient(60rem 40rem at 70% -10%, rgba(99,102,241,0.12), transparent 60%), radial-gradient(40rem 30rem at 10% 110%, rgba(139,92,246,0.10), transparent 55%)",
        }}
      />
      <Sidebar />
      <div className="relative z-10 flex-1 overflow-x-hidden">{children}</div>
    </div>
  );
}
