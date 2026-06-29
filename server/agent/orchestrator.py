"""
Orchestrator agent (Phase 4) — SCAFFOLD. Fill in the TODOs.

This is the new concept for Phase 4: a TRIAGE step. Before any specialist runs,
the orchestrator looks at the user's message, decides what KIND of request it is
(account / knowledge / action), and routes it to the right agent. We chose the
simplest pattern from the build plan — an LLM CLASSIFIER:

    intent = await classify(message)          # one cheap, tool-less LLM call
    async for ev in stream_agent(AGENTS[intent], message, session):
        yield ev                              # delegate to that specialist

Two pieces to build:
  1. classify(message) -> one of INTENTS. A tiny LLM call: no tools, a tight
     system prompt, a hard cap on output tokens. It returns a LABEL, nothing else.
  2. stream_orchestrator(message, session) -> the generator the endpoint streams.
     It classifies, tells the UI where it routed (a new "route" event), then
     delegates to the chosen specialist (account, knowledge, or — since Phase 5 —
     action, which may emit an "approval" event the UI gates on).

Cost note (CLAUDE.md): classify() adds one LLM call per request, so keep it cheap —
max_output_tokens tiny, thinking off. It's classifying, not writing prose.

Event contract — same as the specialists, PLUS one new event so the UI/logs can
show the routing decision:
    {"type": "route", "intent": str}   # NEW: which specialist we picked
    {"type": "tool",  "name": str, "args": dict}
    {"type": "delta", "text": str}
    {"type": "done"}
"""
from __future__ import annotations

from datetime import date

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from agent.agents import AGENTS
from agent.loop import AgentConfig, stream_agent
from utils.constants import USE_MODEL
# The labels classify() is allowed to produce. All three now map to keys in AGENTS
# ("action" is served as of Phase 5).
INTENTS = ("account", "knowledge", "action")
DEFAULT_INTENT = "knowledge"  # safe fallback if the model returns something off-list

CLASSIFIER_SYSTEM_PROMPT = (
    """
    You are a triage classifier for a customer-support assistant. Read the user's
    message and decide which specialist should handle it. Reply with EXACTLY ONE
    word, lowercase, no punctuation, from this list:

      account    - questions about a specific customer's data: their orders,
                   order status, subscriptions, billing records, account details.
      knowledge  - "how do I..." and general policy questions answered from the
                   help center: refund policy, shipping times, support hours, etc.
      action     - requests to DO something with side effects: issue a refund,
                   cancel a subscription, send an email, open a ticket.

    Output only the single word. Do not explain.
    """
)

# ===========================================================================
# PHASE 4 STRETCH — sequential multi-agent ("passes context between agents").
#
# classify() returns ONE intent. For requests that genuinely need two specialists
# in order (look up the order, THEN check the refund policy for it), we generalize
# it to plan(), which returns an ORDERED list. A 1-element plan is just routing —
# so plan() is a strict superset of classify() and supersedes it once you're happy
# with it (you can delete classify() then).
# ===========================================================================
PLANNER_SYSTEM_PROMPT = (
    """
    You are a triage planner for a customer-support assistant. Decide which
    specialist(s) must handle the user's message, and IN WHAT ORDER. Reply with a
    comma-separated list of one or more specialist names, lowercase, nothing else:

      account    - needs a specific customer's data (orders, status, subscriptions).
      knowledge  - needs help-center policy / how-to info (refunds, shipping, hours).
      action     - requests a side-effecting action (refund, cancel, email, ticket).

    Rules:
      - Most messages need exactly ONE specialist -> return that single word.
      - If answering needs the customer's data FIRST and a policy SECOND, return both
        in run order:  account, knowledge
        (Example: "Is my latest order eligible for a refund?" -> account, knowledge —
        you must look up the order before checking the refund policy.)
      - "action" must appear ALONE (the action agent does its own data lookups).
      - Output ONLY the list, e.g.  account   or   account, knowledge
    """
)

GATHER_AND_REPORT_PROMPT = (
    """
    You are an intermediate step within a sequential agent pipeline for a customer-support assistant.
    Your role is to look up and REPORT the concrete details relevant to the
    request (dates, amounts, statuses, ids) and to NOT answer the question,
    refuse, or ask follow-ups.
    These instructions take precedence: even if the request looks like a question you
    cannot answer, do NOT answer or refuse — just look up and report the relevant data.
    """
)


async def plan(message: str) -> list[str]:
    """Return an ORDERED list of specialist names to run for `message`.

    Generalizes classify(): a single-element result behaves exactly like routing;
    a multi-element result is a sequential pipeline.

    Pointers:
      - Make the SAME shape of call as classify(), but with PLANNER_SYSTEM_PROMPT
        and a slightly larger cap (it may return two words) — still tiny, e.g. 20.
      - Parse the reply into a list, preserving order:
          raw = (resp.text or "").strip().lower()
          parsed = [s.strip() for s in raw.split(",")]
      - KEEP ONLY known steps, IN ORDER:  steps = [s for s in parsed if s in INTENTS]
      - Guard rails:
          * if "action" in steps -> return ["action"]   (must run alone, Phase 5)
          * if not steps          -> return [DEFAULT_INTENT]
          * (optional) drop duplicates while keeping order.
      - print(f"[orchestrator] plan -> {steps}") so you can watch it.
    """
    # TODO: implement per the pointers above (mirror classify()'s call, then parse).
    client = genai.Client()

    config = types.GenerateContentConfig(
        max_output_tokens=20,
        system_instruction=PLANNER_SYSTEM_PROMPT,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    resp = await client.aio.models.generate_content(
        model=USE_MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=message)])],
        config=config
    )

    raw = (resp.text or "").strip().lower()
    parsed = [s.strip() for s in raw.split(",")]
    steps = [s for s in parsed if s in INTENTS]
    # The action agent is self-sufficient (it has the read tools too), so it runs
    # ALONE — never as a step in a sequential pipeline. If the planner names it at
    # all, collapse the plan to just the action.
    if "action" in steps:
        return ["action"]
    if not steps:
        return [DEFAULT_INTENT]
    filtered_steps = []
    unique_steps = set()
    for s in steps: 
        if s not in unique_steps:
            unique_steps.add(s)
            filtered_steps.append(s)
    print(f"[orchestrator] plan -> {filtered_steps}")
    return filtered_steps



async def collect_agent_text(
    agent: AgentConfig, message: str, session: AsyncSession
) -> str:
    """Run `agent` to completion and return its final answer as a plain string.

    This is how we capture an EARLIER agent's output to feed as context into a LATER
    one. It drives the same stream_agent loop, but instead of forwarding events to
    the browser it just accumulates the delta text.

    Pointers:
      - parts: list[str] = []
        async for ev in stream_agent(agent, message, session):
            if ev["type"] == "delta": parts.append(ev["text"])
        # ignore tool/done here — you only want the text the agent produced.
      - return "".join(parts)
    """
    # TODO: implement per the pointers above.
    parts: list[str] = []
    async for ev in stream_agent(agent, message, session):
        # get the text that was processed by the agent only
        if ev["type"] == "delta":
            parts.append(ev["text"])
    return "".join(parts)


async def stream_orchestrator(message: str, session: AsyncSession):
    """Plan -> announce route -> run one specialist, OR a sequential pipeline.

    Single-step plans stream straight through (your Phase 4 core, unchanged).
    Multi-step plans run the earlier specialists first to GATHER CONTEXT, then
    stream the LAST specialist with that context folded into its input — this is
    the "passes context between agents" stretch.
    """
    steps = await plan(message)
    # The route event now describes the whole plan, e.g. "account -> knowledge".
    yield {"type": "route", "intent": " -> ".join(steps)}

    # Phase 5: "action" is no longer stubbed. plan() guarantees it arrives ALONE, so
    # it falls through to the FAST PATH below -> stream_agent(ACTION_AGENT, ...). Any
    # approval event the action agent emits forwards straight through to the UI, same
    # as tool/delta/done — the orchestrator re-yields every specialist event verbatim.

    # FAST PATH — a single specialist: stream it directly (unchanged behavior).
    if len(steps) == 1:
        agent_config = AGENTS.get(steps[0], AGENTS[DEFAULT_INTENT])
        async for event in stream_agent(agent_config, message, session):
            yield event
        return

    # SEQUENTIAL PIPELINE (NEW) — two or more specialists, in order.
    # NOTE: the final agent is a specialist with a narrow prompt (e.g. the knowledge
    # agent says "answer ONLY from retrieved chunks"). The order facts you pass in
    # `context` live in the USER message, not the chunks — if the agent ignores them,
    # that's a prompt-tuning lesson, not a bug. (A dedicated synthesis step is the
    # alternative, and it's what a pure PARALLEL fan-out would need.)
    context = ""
    # every agent except the last, collect the results of the previous agent and append
    # to context, so the context can be passed into the following agent
    for step in steps[:-1]:
        # let the UI show which specialist we're consulting (reuse the tool event)
        yield {"type": "tool", "name": f"{step} agent", "args": {}}
        # Always brief the step with the gather-and-report instruction; only the
        # CONTEXT is conditional (empty on the first step, filled on later ones).
        step_input = f"{GATHER_AND_REPORT_PROMPT}\n\nUser request: {message}"
        if context:
            step_input += f"\n{context}"
        answer = await collect_agent_text(AGENTS[step], step_input, session)
        context += f"\n[{step} agent found]:\n{answer}\n"
        print("context: ", context)

    last = steps[-1]
    # LLMs don't know the current date, so they can't judge time-based rules like a
    # refund window on their own — hand it to them explicitly. (date.today() reads
    # the system clock, which matches the seeded 2026 data.)
    final_input = (
        f"{message}\n\n"
        f"Today's date is {date.today().isoformat()} — use it for any time-based "
        f"rule such as a refund window.\n\n"
        f"Context gathered from other specialists:\n{context}"
    )
    async for event in stream_agent(AGENTS[last], final_input, session):
        # only yield at the end to show the final result to the UI
        yield event
