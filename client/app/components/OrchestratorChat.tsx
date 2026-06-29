"use client";

import { useEffect, useRef, useState } from "react";

// Phase 4 — the ORCHESTRATOR chat. One box for everything: it hits the
// /api/orchestrator/chat endpoint, which classifies the message and routes it to
// the account or knowledge specialist internally. It consumes the SAME event
// contract as AccountChat/KnowledgeChat, PLUS routing/approval events:
//   {type:"route",    intent}                  -> which specialist the orchestrator picked
//   {type:"tool",     name, args}              -> a tool is running
//   {type:"tool_result", name, result}         -> NEW (Phase 6): what that tool RETURNED
//   {type:"delta",    text}                    -> a chunk of the answer (typewriter)
//   {type:"approval", pending_id, name, args, summary}  -> NEW (Phase 5): the action
//        agent hit an irreversible, gated tool (refund / email). The server has
//        SAVED its paused state and ENDED this stream. The UI must show Approve/Deny
//        and, on click, RESUME via a brand-new request to /api/orchestrator/resume.
//   {type:"done"}                              -> stream finished
interface AgentEvent {
  type: "route" | "tool" | "tool_result" | "delta" | "approval" | "done";
  intent?: string;
  name?: string;
  args?: Record<string, unknown>;
  text?: string;
  // Phase 6 — only present on "tool_result" events: the dict the tool returned.
  result?: Record<string, unknown>;
  // Phase 5 — only present on "approval" events:
  pending_id?: string;
  summary?: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
}

// Phase 5 adds "awaiting-approval": the stream has paused on a gated action and we
// are blocked on the user clicking Approve/Deny. Input stays disabled (it's already
// !== "idle", so the existing guards cover it) until they decide.
type Status = "idle" | "thinking" | "streaming" | "awaiting-approval";

// The paused action awaiting a decision. `id` is the pending_id we echo back to
// /resume; `summary` is the human-readable line the action tool produced.
interface Pending {
  id: string;
  name?: string;
  summary?: string;
}

// Phase 6 — the AGENT THOUGHTS panel's data model. Today the routing/tool hints
// FLASH by (setRoute/setToolActivity overwrite each other and vanish). Instead we
// KEEP each event of a turn as an ordered list of trace entries and render them in a
// side panel, so a viewer can SEE the agent plan → call tools → pause → finish. The
// events on the wire are unchanged; this is purely a persistent VIEW of the stream
// `consume` already reads.
//   kind   — which event produced this entry (drives the row's icon/color)
//   label  — the human-readable line, e.g. "Routed to account agent"
//   detail — optional extra shown smaller/mono, e.g. a tool's args as JSON
type TraceKind = "route" | "tool" | "tool_result" | "approval" | "done";
interface TraceEntry {
  kind: TraceKind;
  label: string;
  detail?: string;
}

export default function OrchestratorChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [toolActivity, setToolActivity] = useState<string | null>(null);
  // The orchestrator's routing decision, shown so you can SEE the triage step.
  const [route, setRoute] = useState<string | null>(null);
  // Phase 5 — the action awaiting approval (null when nothing is gated).
  const [pending, setPending] = useState<Pending | null>(null);
  // Phase 6 — the running trace shown in the thoughts panel. Reset at the start of
  // each new user turn (onSubmit); appended to as events stream through `consume`.
  // A resume does NOT reset it, so the post-approval narration extends the same trace.
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, toolActivity, route, pending]);

  // The SHARED event handler. Both the initial send AND the resume open an
  // EventSource and hand it here — that's the whole trick to resuming: the
  // narration after Approve/Deny streams through the exact same delta/done logic,
  // landing in the same assistant bubble. Wiring it once keeps the two flows in
  // sync (fix a bug here, both benefit).
  const consume = (es: EventSource) => {
    es.onmessage = (e) => {
      const event: AgentEvent = JSON.parse(e.data);

      // The triage decision arrives first — surface it to the user.
      if (event.type === "route") {
        setRoute(event.intent ?? null);
        // TODO (Phase 6): also record it in the thoughts panel as the FIRST entry of
        // the turn. Append a trace entry (use the functional updater so concurrent
        // events don't clobber each other):
        //   setTrace((t) => [...t, { kind: "route", label: `Planned route: ${event.intent}` }]);
        setTrace((t) => [...t, {kind: "route", label: `Planned route: ${event.intent}`}])
      }

      if (event.type === "tool") {
        setStatus("thinking");
        setToolActivity(`Running ${event.name}…`);
        // TODO (Phase 6): record the tool call. The args are what make the trace
        // concrete (which order, which email) — stringify them into `detail`:
        //   setTrace((t) => [...t, {
        //     kind: "tool",
        //     label: `Called ${event.name}`,
        //     detail: event.args ? JSON.stringify(event.args) : undefined,
        //   }]);
        setTrace((t) => [...t, {
          kind: "tool",
          label: `Called ${event.name}`,
          detail: event.args ? JSON.stringify(event.args) : undefined
        }])
      }

      // Phase 6 — the tool's RETURN value (added in _drive). Render it as its own
      // trace row so the panel shows the full request -> response handshake, right
      // under the matching "Called …" row. Note `result` can be a nested object, so
      // pretty-print it (the 2-space indent) for readability.
      if (event.type === "tool_result") {
        // TODO (Phase 6): append a trace entry for what came back:
        //   setTrace((t) => [...t, {
        //     kind: "tool_result",
        //     label: `${event.name} returned`,
        //     detail: event.result ? JSON.stringify(event.result, null, 2) : undefined,
        //   }]);
        setTrace((t) => [...t, {
          kind: "tool_result",
          label: `${event.name} returned`,
          // the 2 represents a 2 space indent for pretty printing nested JSON in case
          // the result is a nested json
          detail: event.result ? JSON.stringify(event.result, null, 2) : undefined
        }])
      }

      // Phase 5 — the gate. The action agent asked for an irreversible tool; the
      // server saved its state and ENDED this stream. Show the card and wait.
      if (event.type === "approval") {
        // TODO (subpart 8a): handle the approval gate. Pointers:
        //   - stash the paused action so the card can render:
        //       setPending({ id: event.pending_id!, name: event.name, summary: event.summary });
        //   - enter the blocked state + clear the transient hints:
        //       setStatus("awaiting-approval");
        //       setToolActivity(null);
        //   - close THIS EventSource (it's already done server-side; tidy up):
        //       es.close();
        //   - return early so you DON'T fall through to delta/done below.

        // stash the paused action so the approval card can render
        setPending({id: event.pending_id!, name: event.name, summary: event.summary})
        // enter the blocked state + clear transient hints
        setStatus("awaiting-approval")
        setToolActivity(null)

        // TODO (Phase 6): record the pause in the panel so the timeline shows WHY the
        // agent stopped (and the resume narration will extend the same trace):
        //   setTrace((t) => [...t, {
        //     kind: "approval",
        //     label: "Paused for approval",
        //     detail: event.summary,
        //   }]);

        // close the event source and return to avoid falling into the case below
        es.close()
        return
      }

      if (event.type === "delta") {
        setStatus("streaming");
        setToolActivity(null);
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, content: last.content + (event.text ?? "") };
          return next;
        });
      }

      if (event.type === "done") {
        es.close();
        setStatus("idle");
        setToolActivity(null);
        // TODO (Phase 6): optionally cap the timeline with a completion marker:
        //   setTrace((t) => [...t, { kind: "done", label: "Done" }]);
        setTrace((t) => [...t, {kind: "done", label: "Done"}])
      }
    };

    es.onerror = () => {
      es.close();
      setStatus("idle");
      setToolActivity(null);
    };
  };

  const onSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed || status !== "idle") return;

    setMessages((prev) => [
      ...prev,
      { role: "user", content: trimmed },
      { role: "assistant", content: "" },
    ]);
    setInput("");
    setStatus("thinking");
    setToolActivity(null);
    setRoute(null);
    setPending(null);
    // Phase 6: start this turn's thoughts panel fresh. (respond()/resume does NOT
    // reset, so a post-approval continuation extends the same trace.)
    setTrace([]);

    esRef.current?.close();
    const es = new EventSource(
      `http://localhost:8000/api/orchestrator/chat?message=${encodeURIComponent(trimmed)}`
    );
    esRef.current = es;
    consume(es);
  };

  // Phase 5 — RESUME a paused action. Fired by the Approve/Deny buttons. This is a
  // SECOND request (EventSource is GET-only and one-shot), to a DIFFERENT endpoint,
  // carrying the pending_id + decision. Crucially we DON'T push a new assistant
  // bubble: the narration streams into the SAME empty bubble the action agent left
  // behind when it paused, so the conversation continues seamlessly.
  const respond = (decision: "approve" | "deny") => {
    // TODO (subpart 8b): open the resume stream. Pointers:
    //   - guard: if (!pending) return;
    //   - capture the id, then clear the card and re-enter a streaming-ish state:
    //       const pendingId = pending.id;
    //       setPending(null);
    //       setStatus("thinking");
    //   - open a NEW EventSource to the resume endpoint with the id + decision:
    //       esRef.current?.close();
    //       const es = new EventSource(
    //         `http://localhost:8000/api/orchestrator/resume` +
    //         `?pending_id=${encodeURIComponent(pendingId)}&decision=${decision}`
    //       );
    //       esRef.current = es;
    //   - REUSE the shared handler so deltas land in the current bubble:
    //       consume(es);
    if (!pending){
      return
    }
    const pendingId = pending.id
    setPending(null)
    setStatus("thinking")

    // open new event source to the resume endpoint with the id + decision
    esRef.current?.close()
    const es = new EventSource(
      `http://localhost:8000/api/orchestrator/resume` + 
      `?pending_id=${encodeURIComponent(pendingId)}&decision=${decision}`
    ) 
    esRef.current = es
    consume(es)
  };

  useEffect(() => () => esRef.current?.close(), []);

  return (
    <div className="flex flex-row gap-x-4 items-start w-full max-w-5xl mx-auto">
      {/* LEFT: the chat itself (unchanged from Phase 5). */}
      <div className="p-6 flex-1 border rounded-xl shadow-md flex flex-col gap-y-4 bg-white">
        <h2 className="text-xl font-bold">Helpdesk Copilot</h2>
        <p className="text-xs text-gray-400 -mt-2">
          One box — it figures out whether you&apos;re asking about an account or the help center.
        </p>

        <div
          ref={scrollRef}
          className="h-96 overflow-y-auto bg-gray-50 p-3 rounded border flex flex-col gap-y-3 text-sm"
        >
          {messages.length === 0 && (
            <p className="text-gray-400">
              Try: “What&apos;s alice@example.com&apos;s latest order?” or “How long do refunds take?”
            </p>
          )}

          {messages.map((m, i) => (
            <div
              key={i}
              className={m.role === "user" ? "self-end max-w-[85%]" : "self-start max-w-[85%]"}
            >
              <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-0.5">
                {m.role}
              </div>
              <div
                className={`rounded-lg px-3 py-2 whitespace-pre-wrap ${
                  m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-200 text-gray-900"
                }`}
              >
                {m.content || (status !== "idle" ? "…" : "")}
              </div>
            </div>
          ))}

          {route && (
            <div className="self-start text-xs text-indigo-700 italic">
              Routed to the {route} agent
            </div>
          )}
          {toolActivity && (
            <div className="self-start text-xs text-amber-700 italic">{toolActivity}</div>
          )}
          {status === "thinking" && !toolActivity && (
            <div className="self-start text-xs text-gray-500 italic">Thinking…</div>
          )}

          {/* Phase 5 — the approval card. Shown only while an action is gated. */}
          {pending && (
            <div className="self-start max-w-[90%] border border-amber-300 bg-amber-50 rounded-lg p-3 flex flex-col gap-y-2">
              <div className="text-[10px] uppercase tracking-wide text-amber-700">
                Approval required
              </div>
              <div className="text-sm text-amber-900">
                {pending.summary ?? `Run ${pending.name}?`}
              </div>
              <div className="flex flex-row gap-x-2">
                <button
                  onClick={() => respond("approve")}
                  className="bg-green-600 text-white rounded px-3 py-1 text-xs"
                >
                  Approve
                </button>
                <button
                  onClick={() => respond("deny")}
                  className="bg-red-600 text-white rounded px-3 py-1 text-xs"
                >
                  Deny
                </button>
              </div>
            </div>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
          className="flex flex-row gap-x-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={status !== "idle"}
            type="text"
            className="p-2 w-full rounded border disabled:bg-gray-100"
            placeholder="Ask anything — account or help-center…"
          />
          <button
            type="submit"
            disabled={status !== "idle" || !input.trim()}
            className="border rounded px-4 disabled:opacity-50"
          >
            Send
          </button>
        </form>
      </div>

      {/* RIGHT: Phase 6 — the AGENT THOUGHTS panel. A persistent timeline of the
          current turn's events, so the agent's work is VISIBLE instead of flashing
          by. Reads ONLY `trace`; the streaming logic above is untouched. */}
      <aside className="w-80 self-stretch max-h-[36rem] overflow-y-auto p-4 border rounded-xl shadow-md bg-slate-900 text-slate-100 flex flex-col gap-y-2 text-sm">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-300">
          Agent activity
        </h3>

        {trace.length === 0 && (
          <p className="text-slate-500 text-xs">
            The agent&apos;s plan, tool calls, and approvals will appear here.
          </p>
        )}

        {/* TODO (Phase 6): render the trace. Map over `trace` and show each entry as
            a row — `entry.label` as the line, and `entry.detail` (when present) in a
            smaller mono font below it. Pointers:
              trace.map((entry, i) => (
                <div key={i} className="border-l-2 pl-2 border-slate-700">
                  <div>{entry.label}</div>
                  {entry.detail && (
                    <pre className="text-[10px] text-slate-400 whitespace-pre-wrap">
                      {entry.detail}
                    </pre>
                  )}
                </div>
              ))
            Optional polish: vary the border color by entry.kind (route=indigo,
            tool=amber, approval=red, done=green) so the timeline reads at a glance. */}
        <>
        {
          trace.map((entry, i) => {
            return (
              <div key={`entry_${i}`} className="border-l-2 pl-2 border-slate-700">
                <div>{entry.label}</div> 
                {
                  entry.detail && (
                    <pre className="text-[10px] text-slate-400 whitespace-pre-wrap">
                      {entry.detail} 
                    </pre>
                  )
                }
              </div>
            )
          })
        }
        </>
      </aside>
    </div>
  );
}
