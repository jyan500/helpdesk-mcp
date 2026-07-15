"""Phase E0-E1 — a tiny EVAL HARNESS for the helpdesk agent. SCAFFOLD — fill in the E1 TODOs.

WHY THIS EXISTS
---------------
"Does the agent work?" is not a yes/no you can eyeball once. An eval harness turns that
question into a repeatable, scored test suite: feed the agent a fixed set of inputs, capture
what it DID (which tools, which args) and what it SAID (the final text), and score both.

Three separable things are worth scoring for THIS agent (each wants a different scorer):
  1. TRAJECTORY  — did it call the right tools, in a sane order, with the right args?
                   -> programmatic, deterministic, no LLM. (E1, this file)
  2. RETRIEVAL   — for a KB question, did search_docs surface the correct chunk?
                   -> programmatic hit-rate. (E2, later)
  3. ANSWER      — is the final natural-language reply correct & faithful?
                   -> LLM-as-judge, a second cheap model call. (E3, later)

THE SEAM (the whole trick): agent.run() returns a `result`, and result.all_messages() is the
full message history the FRAMEWORK built for you (this replaced your contents.append bookkeeping
in loop.py). Inside it, every tool the model called is a ToolCallPart with .tool_name and
.args_as_dict(). So you can assert on WHAT THE AGENT DID, not just what it said. That is the
valuable, uncommon part of an eval — anyone can grade prose; asserting the trajectory is engineering.

SCOPE OF E0-E1
  - E0 (WORKED below): run every case, extract the trajectory, PRINT trace + answer + tokens.
                       No scoring. Proves the seam works. Run it, watch a trajectory appear.
  - E1 (TODOs below):  score trajectory (subset match) + args (subset match) + absent-tools,
                       then print a pass/fail report table.

DEFERRED ON PURPOSE (don't add here): mutating cases (refund/send_email) need a DB-reset story
and an auto-approve elicitation handler — that's E4. The dataset.json cases are all read-only/
safety so E0-E1 run clean with no DB writes and no [y/N] prompt.

RUN (from server/, as a MODULE so imports resolve):
    .venv/Scripts/python.exe -m evals.harness
Requires: .env (GEMINI_API_KEY) + a seeded DB (the spawned mcp_server hits Postgres).
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from utils.constants import USE_MODEL

# E4 — the auto-approve handler + DB reset helper need these:
from fastmcp.client.elicitation import ElicitResult   # same client-side seam as pydantic_agent.py
from sqlalchemy import delete, select
from db.models import Customer, Order, Ticket
from db.session import AsyncSessionLocal

SERVER_DIR = Path(__file__).resolve().parent.parent
DATASET = Path(__file__).resolve().parent / "dataset.json"


# --- WORKED (E4): AUTO-APPROVE handler. In pydantic_agent.py this asked a human [y/N]; here it
#     rubber-stamps every gated action so mutating cases (issue_refund/send_email) run UNATTENDED.
#     Same signature/return as the real handler — it's the exact Phase 5 client seam, automated.
#     WHY THIS IS SAFE HERE, AND NOWHERE ELSE: the actions are MOCK/sandboxed (a refund just flips
#     a Postgres row; send_email only prints) AND reset_db() undoes them after each case. In a real
#     client, auto-approving an irreversible action would defeat the entire point of the gate —
#     this shortcut is a TEST affordance, not a design. Say exactly that if an interviewer asks.
async def approve_all(message, response_type, params, context):
    return ElicitResult(action="accept")


# --- WORKED: build the agent under test. Same recipe as pydantic_agent.py, but STDIO here so the
#     harness is self-contained (it spawns its own mcp_server subprocess — no "start the HTTP
#     server first" dance). elicitation_handler=approve_all lets gated cases run without blocking.
def build_agent() -> Agent:
    model = GoogleModel(USE_MODEL, provider=GoogleProvider(api_key=os.environ["GEMINI_API_KEY"]))
    toolset = MCPToolset(
        StdioTransport(
            command=sys.executable,
            args=["mcp_server.py"],
            cwd=str(SERVER_DIR),
        ),
        init_timeout=60,  # importing mcp_server loads torch (slow) — same reason as pydantic_agent.py
        elicitation_handler=approve_all,  # E4: auto-accept gated actions (issue_refund/send_email)
    )
    return Agent(
        model,
        toolsets=[toolset],
        instructions=(
            "You are a helpdesk agent. Resolve ids with the lookup tools before acting: "
            "call get_customer(email) first, then get_orders/get_subscription with the id you got. "
            "Never invent an id, email, or order. If a lookup returns nothing, say so — don't guess. "
            "Be concise."
        ),
        model_settings=ModelSettings(max_tokens=1000),
    )


# --- WORKED (E3): the JUDGE. A SECOND agent whose only job is to grade the first agent's answer.
#     Two things make it a "judge" rather than just another chat call:
#       1. output_type=Verdict  -> structured output. result.output is a Verdict instance
#          (verified: .passed / .reason), not free text you'd have to parse.
#       2. no toolsets           -> it only reasons over the text you hand it; it can't act.
#     It's non-deterministic (it's an LLM), so pin max_tokens low and keep rubrics tight — a
#     deliberate contrast to the deterministic E1/E2 scorers.
class Verdict(BaseModel):
    passed: bool
    reason: str  # one line — why it passed/failed. Surface it in the report when a case is red.


def build_judge() -> Agent:
    model = GoogleModel(USE_MODEL, provider=GoogleProvider(api_key=os.environ["GEMINI_API_KEY"]))
    return Agent(
        model,
        output_type=Verdict,  # <- the structured-output kwarg (2.5; was result_type in older versions)
        instructions=(
            "You are a strict grader. You are given a RUBRIC, the user's QUESTION, and the "
            "agent's ANSWER. Return passed=true ONLY if the ANSWER satisfies the RUBRIC. Judge "
            "only against the rubric — not your own opinion of a good answer. Give a one-line reason."
        ),
        model_settings=ModelSettings(max_tokens=200),  # cost + latency guardrail; judging is cheap
    )


# --- WORKED: THE SEAM. Walk the message history and pull out the tool trajectory. Each model turn
#     is a ModelResponse whose .parts may include ToolCallParts; everything else (text, tool
#     returns) we ignore here. Returns e.g. [{"tool": "get_customer", "args": {"email": "..."}}].
def extract_tool_calls(messages) -> list[dict]:
    calls = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append({"tool": part.tool_name, "args": part.args_as_dict()})
    return calls


# --- WORKED (E2): the RETURN side of the seam. extract_tool_calls captured what the agent SENT;
#     this captures what each tool SENT BACK. A tool's return arrives as a ToolReturnPart whose
#     .content is already the deserialized Python value (for search_docs: {"count", "chunks"}).
#     Returns e.g. [{"tool": "search_docs", "content": {"count": 5, "chunks": [...]}}].
def extract_tool_returns(messages) -> list[dict]:
    returns = []
    for msg in messages:
        for part in getattr(msg, "parts", []):  # ToolReturnParts ride in ModelRequest messages
            if isinstance(part, ToolReturnPart):
                returns.append({"tool": part.tool_name, "content": part.content})
    return returns


# --- WORKED: run ONE case. Returns a plain dict of raw observations (no scoring — the scorers do that).
async def run_case(agent: Agent, case: dict) -> dict:
    t0 = time.perf_counter()
    result = await agent.run(case["input"], usage_limits=UsageLimits(request_limit=6))
    elapsed = time.perf_counter() - t0
    messages = result.all_messages()
    calls = extract_tool_calls(messages)
    return {
        "id": case["id"],
        "trace": calls,                              # list[{tool,args}]
        "tools": [c["tool"] for c in calls],         # just the names
        "returns": extract_tool_returns(messages),   # list[{tool,content}] — E2 retrieval scorer reads this
        "answer": result.output,
        "usage": result.usage,  # attribute in pydantic-ai 2.5, not a call
        "elapsed_s": round(elapsed, 2),
    }


# --- WORKED (E4): reset the rows the MUTATING cases touch, so every case starts from a known state.
#     Why not just re-run db.seed? Two reasons:
#       1. seed is destructive to the WHOLE db, including the ingested KB (Article/DocChunk) — a
#          reseed would break the E2 retrieval case until you re-ingest (slow, loads torch).
#       2. the agent runs in a SEPARATE PROCESS (the stdio mcp_server) with its OWN db sessions, so
#          you CAN'T wrap its writes in a transaction and roll back — rollback can't cross that
#          process boundary. (That's the deep reason test-isolation is harder here than for a plain
#          in-process function; worth being able to explain.)
#     So we do the pragmatic thing — undo exactly what our tools mutate:
#       - issue_refund flips the customer's LATEST order status -> restore it to its seed value
#         ('shipped'). We LOOK IT UP rather than hardcode an id, matching what the rubric tests.
#       - create_ticket inserts Ticket rows -> delete them.
#       - send_email touches nothing        -> nothing to undo.
REFUND_CUSTOMER_EMAIL = "alice@example.com"

async def reset_db() -> None:
    async with AsyncSessionLocal() as session:
        # the latest order = most recent created_at for the refund case's customer
        latest = (await session.execute(
            select(Order)
            .join(Customer)
            .where(Customer.email == REFUND_CUSTOMER_EMAIL)
            .order_by(Order.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is not None:
            latest.status = "shipped"  # its seeded value (see db/seed.py: the 2026-06-10 keyboard)
        await session.execute(delete(Ticket))
        await session.commit()


# ============================= E4 — YOUR TURN (isolation + report) =========================
# Two wiring tasks (the mutating 'refund-latest' case is already in dataset.json):
#   1. ISOLATION: in main(), call `await reset_db()` at the TOP of the per-case loop, BEFORE
#      run_case — so every case starts from a known state. (This also cures the stale 'refunded'
#      ghost you saw earlier in order-status.) reset_db needs no toolset, so it does NOT have to be
#      inside the `async with agent` block — but calling it at the loop top is simplest.
#   2. REPORT FOOTER: extend report() with a summary line — "<n>/<m> passed" + total tokens +
#      total latency. The numbers live on each obs: obs['usage'].total_tokens and obs['elapsed_s'].
#      Simplest wiring: pass `observations` into report() alongside `scored` (or zip them) and sum.
#      (This counts the agent-under-test's tokens; the E3 judge spends a little more on top.)
# ==========================================================================================


# =============================== E1 — YOUR TURN (type these) ===============================
# The scorers below are pure functions over the observations run_case already collected. They
# take a `case` (the expectations from dataset.json) and an `obs` (what actually happened) and
# return a bool. Keep them small and deterministic — no LLM, no I/O.

def score_trajectory(case: dict, obs: dict) -> bool:
    """PASS if every tool in case['expect_tools'] appears in obs['tools'].
    Use SUBSET, not equality: the model may add a harmless extra lookup and that's fine.
    Hint: set(case.get('expect_tools', [])).issubset(set(obs['tools']))."""
    # TODO E1a: implement the subset check. If 'expect_tools' is missing, treat as pass (True).
    return set(case.get("expect_tools", [])).issubset(set(obs["tools"]))


def score_absent(case: dict, obs: dict) -> bool:
    """PASS if NONE of case['expect_tools_absent'] appear in obs['tools'] — the safety assertion
    (e.g. the unknown-customer case must never call get_orders).
    Hint: no overlap between the two sets -> set(absent).isdisjoint(set(obs['tools']))."""
    # TODO E1b: implement. Missing 'expect_tools_absent' -> pass (True).
    return set(case.get("expect_tools_absent", [])).isdisjoint(set(obs["tools"]))


def score_args(case: dict, obs: dict) -> bool:
    """PASS if, for each tool named in case['expect_args'], SOME call to that tool in obs['trace']
    has args that include the expected key/values (subset — the call may carry extra args).
    Steps:
      1. If 'expect_args' is missing -> pass (True).
      2. For each (tool_name, expected_args) in case['expect_args'].items():
           find the calls in obs['trace'] where c['tool'] == tool_name;
           pass this tool if ANY such call's args is a superset of expected_args
           (i.e. every k,v in expected_args equals call_args.get(k)).
      3. All named tools must pass.
    Hint for the subset test: all(call_args.get(k) == v for k, v in expected_args.items())."""
    # TODO E1c: implement.
    if "expect_args" not in case:
        return True
    result = True
    for tool_name, expected_args in case["expect_args"].items():
        calls = [c for c in obs["trace"] if c["tool"] == tool_name]
        matched = any(
            all(call["args"].get(k) == v for k,v in expected_args.items())
            for call in calls
        )
        result = result and matched
    return result


# ============================= E2 — YOUR TURN (retrieval eval) =============================
# The gap this closes: right now an empty KB and a genuinely-bad retrieval both surface only as a
# sad final answer. This scorer asserts DIRECTLY that search_docs returned the right chunk, so a
# retrieval regression (e.g. the KB wasn't re-ingested after a reseed) fails loudly on its own row.

def score_retrieval(case: dict, obs: dict) -> bool:
    """PASS if the expected article slug (case['expect_retrieval']) appears among the chunks that
    search_docs actually RETURNED. Reads obs['returns'] (the return-side seam), not the answer.
    Steps:
      1. If 'expect_retrieval' is missing -> pass (True).
      2. Collect the slugs search_docs returned: for each r in obs['returns'] where
         r['tool'] == 'search_docs', r['content'] is {'count', 'chunks'}; each chunk is a dict
         with a 'slug'. Gather every chunk's slug into a set.
      3. PASS if case['expect_retrieval'] is in that set.
    Hint: slugs = {ch['slug'] for r in obs['returns'] if r['tool'] == 'search_docs'
                                for ch in r['content']['chunks']}
    """
    # TODO E2: implement.
    if not ("expect_retrieval" in case):
        return True
    slug_set = set()
    for r in obs["returns"]:
        if r["tool"] == "search_docs":
            content = r["content"]
            for chunk in content["chunks"]:
                slug_set.add(chunk["slug"])
    return case["expect_retrieval"] in slug_set


# TODO E2-wire: fold retrieval into the verdict + table:
#   - in score_case: add   retrieval = score_retrieval(case, obs)   to the dict, and AND it into
#     `passed` (passed = trajectory and absent and args and retrieval).
#   - in report: add a `retrieval` column so the refund-policy row shows it.
# ==========================================================================================


# ============================= E3 — YOUR TURN (LLM-as-judge) ===============================
# The last dimension: ANSWER QUALITY. E1/E2 scored behavior; this grades the prose against the
# case's judge_rubric. UNLIKE the other scorers this one is ASYNC (it calls the judge agent) and
# non-deterministic — which is why it's a separate scorer you can leave off a run if you want the
# fast, deterministic checks only.

async def score_answer(case: dict, obs: dict, judge: Agent) -> bool:
    """PASS if the judge agent rules that obs['answer'] satisfies case['judge_rubric'].
    Steps:
      1. rubric = case.get('judge_rubric'); if there's no rubric -> pass (True) (skip judging).
      2. Build a prompt string with the RUBRIC, the QUESTION (case['input']), and the ANSWER
         (obs['answer']) clearly labeled — the judge's instructions expect those three.
      3. result = await judge.run(prompt).  result.output is a Verdict (structured).
      4. Return result.output.passed.  (Tip: you may also want result.output.reason for the report
         when a case is red — return it too, or stash it on obs, if you extend the row later.)
    Note: keep this deterministic-ish by trusting the rubric; don't add retries/temperature here."""
    # TODO E3: implement.
    rubric = case.get("judge_rubric")
    if not rubric:
        return True
    prompt = f"""
    You are a strict grader. You are given a RUBRIC, the user's QUESTION, and the 
    agent's ANSWER. Return passed=true ONLY if the ANSWER satisfies the RUBRIC. Judge
    only against the rubric — not your own opinion of a good answer. Give a one-line reason.

    rubric: {rubric}
    question: {case["input"]}
    answer: {obs["answer"]}
    """
    result = await judge.run(prompt)
    return result.output.passed

# TODO E3-wire: this one changes a few call sites because it's async:
#   - score_case: make it `async def score_case(case, obs, judge)`, add
#       answer = await score_answer(case, obs, judge)
#     to the dict and AND it into `passed`.
#   - report: add an `answer` column.
#   - main: build the judge ONCE  ->  judge = build_judge()  (outside the loop; it needs no toolset,
#     so it can live outside the `async with agent` block), then await each score_case:
#       scored = [await score_case(case, obs, judge) for case, obs in zip(cases, observations)]
#     (an `await` inside a list comprehension is fine — main is already async).
# ==========================================================================================





async def score_case(case: dict, obs: dict, judge: Agent) -> dict:
    """Combine the scorers into one verdict per case."""
    # TODO E1d: call the three scorers, collect their bools, and compute an overall pass =
    #   (all of them True). Return a small dict the report can render, e.g.:
    #   {"id": case["id"], "trajectory": ..., "absent": ..., "args": ..., "passed": ...}
    trajectory = score_trajectory(case, obs)
    absent =  score_absent(case, obs)
    args = score_args(case, obs)
    retrieval = score_retrieval(case, obs)
    answer = await score_answer(case, obs, judge)
    return {
        "id": case["id"],
        "trajectory": trajectory,
        "retrieval": retrieval,
        "absent": absent,
        "args": args,
        "answer": answer,
        "passed": trajectory and absent and args and retrieval and answer
    }


def report(scored: list[dict], observations: List[dict]) -> None:
    """Print a pass/fail table + a summary line."""
    # TODO E1e: print one row per case (id, the three sub-checks, PASS/FAIL), then a footer like
    #   "N/M passed". Keep it plain text — a screenshot of this is the artifact for the README.
    #   (A simple f-string table with fixed-width columns is enough; no library needed.)
    print("****** Pass/Fail Table ******")
    for score in scored:
        print(f"id: {score['id']} trajectory: {score['trajectory']} absent: {score['absent']} args: {score['args']} retrieval: {score["retrieval"]} answer: {score["answer"]} passed: {score['passed']}")

    passed = sum(1 for s in scored if s["passed"])
    total_tokens = sum([obs["usage"].total_tokens for obs in observations])
    total_elapsed = sum([obs["elapsed_s"] for obs in observations])
    print(f"\n{passed}/{len(scored)} passed | {total_tokens} tokens | {total_elapsed:.2f}s")

# ==========================================================================================


async def main() -> None:
    cases = json.loads(DATASET.read_text())["cases"]
    agent = build_agent()
    judge = build_judge()

    async with agent:  # opens the toolset once (spawns + handshakes mcp_server), reused across cases
        observations = []
        for case in cases:
            await reset_db()
            obs = await run_case(agent, case)
            observations.append(obs)

            # --- E0: just SEE it. Comment this block out once E1's report() works. ---
            print(f"\n=== {obs['id']} ===")
            print("  input :", case["input"])
            print("  tools :", " -> ".join(obs["tools"]) or "(none)")
            print("  args  :", [c["args"] for c in obs["trace"]])
            print("  answer:", obs["answer"])
            print("  usage :", obs["usage"], f"({obs['elapsed_s']}s)")

    # --- E1: uncomment once the scorers + report are implemented. ---
    scored = [await score_case(case, obs, judge) for case, obs in zip(cases, observations)]
    report(scored, observations)


if __name__ == "__main__":
    asyncio.run(main())
