"""
The hand-rolled agent loop.

Phases 2–3 left this file with three near-identical loops (run_account_agent,
stream_account_agent, stream_knowledge_agent). The only things that EVER differed
between the two streaming ones were:
    (a) the system prompt,
    (b) the tool declarations passed to the model,
    (c) the tool registry the loop dispatches through.

Phase 4 cashes in the promise those functions kept making ("Phase 4 is where we
factor the shared loop out"). Your job in this file:

    1. Describe a specialist agent as DATA  -> fill in `AgentConfig`.
    2. Write the ONE generic streaming loop -> fill in `stream_agent`, by PORTING
       your existing stream_account_agent and swapping the 3 hard-coded bits for
       fields off `agent`.
    3. Once stream_agent works, the two old streaming functions below become dead
       code — delete them (they're kept now ONLY as the thing you port from).

Still by hand — no agent framework (see the build plan's framework note).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from agent.pending import (
    create_pending_action,
    deserialize_contents,
    load_pending_action,
    mark_pending_action,
)
from tools.account import ACCOUNT_TOOL_DECLS, TOOLS
from tools.action import describe_action
from tools.knowledge import KNOWLEDGE_TOOL_DECLS
from tools.knowledge import TOOLS as KNOWLEDGE_TOOLS
from utils.constants import USE_MODEL
# NOTE: AGENTS (agent/agents.py) is imported LAZILY inside resume_agent, not here —
# agents.py imports FROM this module, so a top-level import would be circular.

MAX_ITERS = 6  # cost guardrail: never loop forever (CLAUDE.md cost rule)
EMPTY_RETRY_LIMIT = 4 # Flash-Lite intermittently ends a turn (finish_reason=STOP) with
                      # no content; retrying the same request usually succeeds, so allow a
                      # few attempts before giving up.

ACCOUNT_SYSTEM_PROMPT = (
    """
    You are a helpdesk agent that answers questions about customers, their orders and subscriptions.
    To answer questions about a customer, you must first call get_customer(email) to get the id,
    then call get_orders/get_subscription with that id.
    If get_customer returns found=false, say you couldn't find the customer instead of inventing data.
    Be concise. Provide the customer, order, and subscription information relevant to the
    request. If you are explicitly asked only to look up and report data (e.g. as part of a
    larger workflow), do exactly that — report the data and don't try to answer questions
    that are outside your scope.
    """
)

# The whole point of RAG is grounding: the model must answer from RETRIEVED text,
# not its own memory, and tell the user WHERE the answer came from.
KNOWLEDGE_SYSTEM_PROMPT = (
    """
    You are a helpdesk knowledge agent. You answer "how do I..." and policy
    questions (refunds, shipping, billing, account/login, support hours) using the
    company's help-center articles.

    You MUST call search_docs FIRST on every policy/how-to question, before writing
    any answer — even when customer data or context has already been provided to you.
    That provided context is NOT a substitute for the policy: it contains the
    customer's data (e.g. their orders), never the rules themselves. So never answer a
    policy question from prior knowledge or from provided context alone — always
    retrieve first. After retrieving, answer using ONLY the information in the returned
    chunks, combined with any customer data you were given. If the chunks don't contain
    the answer, say you couldn't find it in the help center rather than guessing.

    For questions about whether something QUALIFIES under a policy (e.g. refund
    eligibility), decide ONLY from conditions the retrieved policy explicitly
    states; do not invent or infer disqualifiers. In particular: an order's
    fulfillment status (e.g. "shipped" or "delivered") does NOT affect refund
    eligibility unless the policy says so, and "shipping charges are non-refundable"
    means the shipping FEE is not refunded — it does NOT mean a shipped order can't
    be refunded. Work through each stated condition in turn (is it within the time
    window from the purchase date? is it final-sale? etc.) and base your verdict on
    those. If a condition can't be determined from the information provided, say so
    instead of assuming the worst.

    Cite your source: end your answer with the title of the article you used,
    e.g. (Source: Refunds & Returns). Be concise and answer the actual question.
    """
)

# Phase 5 — the ACTION agent. Unlike the read-only specialists, it can change the
# world (refund, ticket, email), so its prompt is about doing the RIGHT action with
# the RIGHT id, not about answering questions. Approval is enforced by the loop
# (requires_approval), NOT by the prompt — so this only needs to tell the model how
# to gather ids and what to say afterward. Tune it freely; prompt-tuning is the lesson.
ACTION_SYSTEM_PROMPT = (
    """
    You are a helpdesk action agent. You DO things on the user's behalf: issue
    refunds, open support tickets, and send emails.

    Many actions need a concrete id the user didn't give you. Resolve it FIRST with
    the lookup tools: call get_customer(email) to get the customer id, then
    get_orders(customer_id) to find the specific order. NEVER invent an order id,
    customer id, or email — if you can't determine the exact target from the data,
    say what's missing instead of guessing.

    Once you have the concrete arguments, call the single action tool that does what
    the user asked (issue_refund, create_ticket, or send_email). Do exactly what was
    requested — one action — and don't take extra actions you weren't asked for.

    After a tool returns, report the outcome plainly: confirm what was done (include
    the refund/ticket id) on success, or explain what went wrong (e.g. the order was
    already refunded or couldn't be found). Be concise.
    """
)


# ===========================================================================
# PHASE 4 — STEP 1: AgentConfig — a specialist agent described as DATA.
#
# This is the heart of the refactor. Instead of one streaming function per agent,
# each specialist becomes an instance of this dataclass; the generic loop reads
# its fields. Adding Phase 5's Action agent later then becomes "make one more
# AgentConfig", not "copy the whole loop again".
#
# TODO: declare the four fields the loop needs. Look at what stream_account_agent
#   and stream_knowledge_agent below ACTUALLY differ on — those differences ARE
#   the fields:
#       name: str                                    # "account"/"knowledge" — for logs + routing
#       system_prompt: str                           # -> goes to config.system_instruction
#       tool_decls: list[types.FunctionDeclaration]  # -> goes to types.Tool(function_declarations=...)
#       tools: dict[str, Callable[..., Awaitable[dict]]]  # name -> async callable to dispatch to
#   (Awaitable/Callable are imported up top so you can type `tools` precisely.)
# ===========================================================================
@dataclass(frozen=True)
class AgentConfig:
    name: str
    system_prompt: str
    tool_decls: list[types.FunctionDeclaration]
    tools: dict[str, Callable[..., Awaitable[dict]]]
    # PHASE 5: the names of this agent's tools that are IRREVERSIBLE and must pause
    # for human approval before they run (e.g. action's issue_refund/send_email).
    # Defaults to empty, so the account/knowledge agents are completely unchanged —
    # only the action agent will pass a non-empty set (= tools.action.REQUIRES_APPROVAL).
    requires_approval: frozenset[str] = frozenset()


# ===========================================================================
# PHASE 4 — STEP 2: stream_agent — the ONE streaming loop, parameterized.
#
# Port your stream_account_agent (below) almost verbatim. The ONLY changes:
#   - config.system_instruction      = agent.system_prompt   (was the constant)
#   - types.Tool(function_declarations=agent.tool_decls)     (was ACCOUNT_TOOL_DECLS)
#   - dispatch: await agent.tools[function_call.name](...)   (was TOOLS[...])
# Everything else — the chunk-by-chunk consume, the None guards, the two-append
# feed-back, the empty-retry + iteration-cap handling — is IDENTICAL. Don't
# re-derive it; move it.
#
# Event contract stays the same so the SAME frontend can render ANY agent:
#     {"type": "tool",  "name": str, "args": dict}
#     {"type": "delta", "text": str}
#     {"type": "done"}
# ===========================================================================
async def stream_agent(agent: AgentConfig, message: str, session: AsyncSession):
    """Async generator: drive `agent`'s loop from a fresh user message.

    Thin wrapper since Phase 5: seed the conversation with the user's message, then
    hand off to _drive (the shared loop). The loop was split out of here so that
    resume_agent can re-enter the SAME loop from a SAVED conversation after an
    approval pause — see _drive and resume_agent below.

    Event contract (unchanged for the UI, plus the new "approval" event from _drive):
        {"type": "approval", "pending_id": str, "name": str, "args": dict, "summary": str}
        {"type": "tool",  "name": str, "args": dict}
        {"type": "delta", "text": str}
        {"type": "done"}
    """
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=message)]),
    ]
    async for event in _drive(agent, contents, session):
        yield event


# ===========================================================================
# PHASE 5 — _drive: the shared tool-calling loop, started from an EXISTING
# `contents` history rather than building it. Two callers:
#   stream_agent  -> seeds contents with the user message (a fresh run).
#   resume_agent  -> rebuilds contents from a saved PendingAction + the approve/deny
#                    outcome (a continuation after a pause).
# Body is the Phase 4 loop verbatim, with ONE new branch: an approval-gated tool
# PAUSES (persist + yield "approval" + return) instead of executing.
# ===========================================================================
async def _drive(agent: AgentConfig, contents: list[types.Content], session: AsyncSession):
    """Run the tool-calling loop over `contents`, yielding SSE events."""
    client = genai.Client()

    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=agent.tool_decls)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        max_output_tokens=1000,
        system_instruction=agent.system_prompt,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    empty_retries = 0
    for i in range(MAX_ITERS):
        stream = await client.aio.models.generate_content_stream(
        model=USE_MODEL, contents=contents, config=config)

        function_call = None
        produced_text = False
        finish_reason = None       # why the model ended its turn (diagnostic)
        prompt_feedback = None     # set if the PROMPT itself was blocked (safety)
        thought_signature = None   # Gemini 3: opaque token that MUST be echoed back
                                   # with the function call or the API 400s
        async for chunk in stream:
            # prompt-level block: no candidates at all, but maybe prompt_feedback
            if not chunk.candidates:
                if getattr(chunk, "prompt_feedback", None):
                    prompt_feedback = chunk.prompt_feedback
                continue
            cand = chunk.candidates[0]
            # finish_reason rides on a metadata-only chunk (content=None), so grab it
            # BEFORE the parts guard below or we'd never see it.
            if cand.finish_reason:
                finish_reason = cand.finish_reason
            content = cand.content
            # some chunks contain only metadata (i.e finish reason, usage, etc) but no parts
            if content is None or content.parts is None:
                continue
            for part in content.parts:
                if part.function_call:
                    function_call = part.function_call
                    # Gemini 3 rides a thought_signature on the function-call part;
                    # keep it so we can send it back on the model turn below.
                    thought_signature = part.thought_signature
                elif part.text:
                    produced_text = True
                    yield {"type": "delta", "text": part.text}

        # there IS a tool call.
        if function_call is not None:
            # The model turn that REQUESTED the tool. BOTH the pause path and the
            # execute path need this appended to `contents`, with thought_signature
            # echoed back (Gemini 3 requires it).
            model_turn = types.Content(
                role="model",
                parts=[types.Part(
                    function_call=function_call,
                    thought_signature=thought_signature,
                )],
            )

            # PHASE 5 GATE (SCAFFOLD — fill in the TODO): an approval-gated tool must
            # NOT run here. Freeze the agent and hand the decision to the user.
            if function_call.name in agent.requires_approval:
                # TODO: implement the pause. Pointers:
                #   contents.append(model_turn)   # saved history must include the request
                #   pending_id = await create_pending_action(
                #       session, agent.name, function_call, contents)
                #   yield {
                #       "type": "approval",
                #       "pending_id": pending_id,
                #       "name": function_call.name,
                #       "args": dict(function_call.args),
                #       "summary": describe_action(
                #           function_call.name, dict(function_call.args)),
                #   }
                #   return     # suspend: stream ends; resume_agent continues later
                contents.append(model_turn) # include saved history
                pending_id = await create_pending_action(
                    session, agent.name, function_call, contents
                )
                yield {
                    "type": "approval",
                    "pending_id": pending_id,
                    "name": function_call.name,
                    "args": dict(function_call.args),
                    "summary": describe_action(
                        function_call.name, dict(function_call.args)
                    )
                }
                return

            # UNGATED path (Phase 4, unchanged): announce -> execute -> feed back.
            yield {"type": "tool", "name": function_call.name, "args": dict(function_call.args)}
            result = await agent.tools[function_call.name](session, **function_call.args)
            print(f"{agent.name} [iter {i}] {function_call.name}({dict(function_call.args)}) -> {result}")

            # PHASE 6 (thoughts panel): surface what the tool RETURNED, not just that it
            # ran. This is the SAME `result` we feed back to the model below — we just
            # also forward it to the UI so the panel shows the full request -> response
            # handshake from Phase 1 (model asks -> we run -> here's the result -> model
            # speaks). The dict is already JSON-safe (the tools return model_dump(mode=
            # "json")), so it round-trips over SSE as-is — no extra serialization.
            # TODO: yield the result event (mirror the "tool" yield above):
            #   yield {"type": "tool_result", "name": function_call.name, "result": result}
            yield {"type": "tool_result", "name": function_call.name, "result": result}

            contents.append(model_turn)
            contents.append(types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=function_call.name, response={"result": result}
                    )
                ]
            ))
            continue

        if produced_text:
            yield {"type": "done"}
            return

        empty_retries += 1
        if empty_retries <= EMPTY_RETRY_LIMIT:
            print(
                f"{agent.name} [iter {i}] empty turn "
                f"(finish_reason={finish_reason}, prompt_feedback={prompt_feedback}) "
                f"- retrying ({empty_retries})"
            )
            continue
        yield {"type": "delta", "text": "Sorry, I couldn't generate a response. Please try again."}
        yield {"type": "done"}
        return

    yield {"type": "delta", "text": "Sorry — I couldn't complete that within the step limit."}
    yield {"type": "done"}


# ===========================================================================
# PHASE 5 — resume_agent: continue a PAUSED agent after the user decides.
#
# The other half of the gate. _drive suspended by saving a PendingAction and ending
# the stream. The /resume endpoint (subpart 7) calls this with the pending_id and
# the user's decision. We rebuild the EXACT conversation, append the tool's result
# (approve) or a "declined" note (deny) as the function_response the loop was waiting
# for, and re-enter the SAME _drive loop so the model narrates the outcome.
#
# This is the phase checkpoint made concrete: a "paused agent" is just a saved
# `contents` history + a pending tool call; resuming is "append the missing
# function_response and keep looping."
# ===========================================================================
async def resume_agent(pending_id: str, decision: str, session: AsyncSession):
    """Async generator: finish a gated action the user Approved or Denied.

    Pointers:
      - Lazy import to dodge the circular dependency (agents.py imports THIS module):
            from agent.agents import AGENTS
      - Load + thaw the paused agent:
            row = await load_pending_action(session, pending_id)
            if row is None:                      # unknown / already-handled id
                yield {"type": "delta", "text": "That approval request was not found."}
                yield {"type": "done"}
                return
            agent = AGENTS[row.agent_name]
            contents = deserialize_contents(row.contents)   # includes the model turn w/ the call
      - Decide and produce the tool result:
            approved = decision == "approve"
            if approved:
                result = await agent.tools[row.tool_name](session, **row.tool_args)
            else:
                result = {"status": "denied",
                          "message": "The user declined this action; it was not performed."}
            await mark_pending_action(session, pending_id, "approved" if approved else "denied")
      - Feed that result back as the function_response the loop was waiting on, then
        re-enter the loop so the model can speak:
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(
                    name=row.tool_name, response={"result": result})],
            ))
            async for event in _drive(agent, contents, session):
                yield event
      - (Optional Phase 6 hardening: if row.status != "pending", refuse to re-run —
        guards a double-approve. Skip for now.)
    """
    # TODO: implement per the pointers above.
    from agent.agents import AGENTS
    row = await load_pending_action(session, pending_id)
    # unknown/already handled
    if row is None:
        yield {"type": "delta", "text": "Approval request was not found"}
        yield {"type": "done"}
        return
    agent = AGENTS[row.agent_name]
    contents = deserialize_contents(row.contents) # includes the model turn w/ any call
    approved = decision == "approve"
    if approved:
        # get the pending tool call based on the tool name
        result = await agent.tools[row.tool_name](session, **row.tool_args)
    else:
        result = {
            "status": "denied",
            "message": "The user declined this action; it was not performed."
        }
    await mark_pending_action(session, pending_id, "approved" if approved else "denied")
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_function_response(
            name=row.tool_name, response={"result": result}
        )]
    ))
    async for event in _drive(agent, contents, session):
        yield event


# ===========================================================================
# run_account_agent — Phase 2 NON-streaming reference (kept unchanged).
#
# Returns one final string instead of yielding events. The simplest readable
# version of the tool-calling handshake; keep it as a teaching reference.
# ===========================================================================
async def run_account_agent(message: str, session: AsyncSession) -> str:
    """Run the tool-calling loop until the model gives a final text answer."""
    client = genai.Client()

    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=ACCOUNT_TOOL_DECLS)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        max_output_tokens=1000,
        system_instruction=ACCOUNT_SYSTEM_PROMPT,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=message)]),
    ]
    empty_retries = 0
    for i in range(MAX_ITERS):
        response = await client.aio.models.generate_content(
            model=USE_MODEL, contents=contents, config=config
        )

        model_turn = response.candidates[0].content
        parts = model_turn.parts if (model_turn and model_turn.parts) else []

        function_call = None
        final_text = []
        for part in parts:
            if part.function_call:
                function_call = part.function_call
            elif part.text:
                final_text.append(part.text)

        if not function_call:
            if final_text:
                return "\n".join(final_text)
            empty_retries += 1
            if empty_retries <= EMPTY_RETRY_LIMIT:
                continue
            return "Sorry, I couldn't generate a response. Please try again."

        fn = TOOLS[function_call.name]
        result = await fn(session, **function_call.args)

        contents.append(model_turn)
        fr = types.Part.from_function_response(
            name=function_call.name, response={"result": result})
        contents.append(types.Content(role="user", parts=[fr]))

    return "Sorry — I couldn't complete that within the step limit."


if __name__ == "__main__":
    import asyncio

    from db.session import AsyncSessionLocal, engine
    from agent.agents import ACTION_AGENT

    async def _smoke():
        async with AsyncSessionLocal() as session:
            # Once stream_agent + agent/agents.py are filled in, switch this to:
            #   from agent.agents import AGENTS
            #   async for event in stream_agent(AGENTS["account"], "...", session):
            async for event in stream_agent(
                ACTION_AGENT,
                "Please refund alice@example.com's latest order",
                session
            ):
                print("EVENT: ", event)
        await engine.dispose()

    asyncio.run(_smoke())
