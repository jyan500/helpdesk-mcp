"use client"
import OrchestratorChat from "@/app/components/OrchestratorChat"

export default function Home() {
  return (
    <div className="text-slate-600 flex flex-col flex-1 items-center justify-center gap-y-8 bg-zinc-50 font-sans dark:bg-black p-6">
      {/* Phase 4 deliverable: the single orchestrated box that routes internally. */}
      <OrchestratorChat/>
    </div>
  );
}
