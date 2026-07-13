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

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from utils.constants import USE_MODEL

SERVER_DIR = Path(__file__).resolve().parent.parent
DATASET = Path(__file__).resolve().parent / "dataset.json"


# --- WORKED: build the agent under test. Same recipe as pydantic_agent.py, but STDIO here so the
#     harness is self-contained (it spawns its own mcp_server subprocess — no "start the HTTP
#     server first" dance). No elicitation_handler: the E0/E1 cases never hit a gated tool. -------
def build_agent() -> Agent:
    model = GoogleModel(USE_MODEL, provider=GoogleProvider(api_key=os.environ["GEMINI_API_KEY"]))
    toolset = MCPToolset(
        StdioTransport(
            command=sys.executable,
            args=["mcp_server.py"],
            cwd=str(SERVER_DIR),
        ),
        init_timeout=60,  # importing mcp_server loads torch (slow) — same reason as pydantic_agent.py
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





def score_case(case: dict, obs: dict) -> dict:
    """Combine the scorers into one verdict per case."""
    # TODO E1d: call the three scorers, collect their bools, and compute an overall pass =
    #   (all of them True). Return a small dict the report can render, e.g.:
    #   {"id": case["id"], "trajectory": ..., "absent": ..., "args": ..., "passed": ...}
    trajectory = score_trajectory(case, obs)
    absent =  score_absent(case, obs)
    args = score_args(case, obs)
    retrieval = score_retrieval(case, obs)
    return {
        "id": case["id"],
        "trajectory": trajectory,
        "retrieval": retrieval,
        "absent": absent,
        "args": args,
        "passed": trajectory and absent and args and retrieval
    }


def report(scored: list[dict]) -> None:
    """Print a pass/fail table + a summary line."""
    # TODO E1e: print one row per case (id, the three sub-checks, PASS/FAIL), then a footer like
    #   "N/M passed". Keep it plain text — a screenshot of this is the artifact for the README.
    #   (A simple f-string table with fixed-width columns is enough; no library needed.)
    print("****** Pass/Fail Table ******")
    for score in scored:
        print(f"id: {score['id']} trajectory: {score['trajectory']} absent: {score['absent']} args: {score['args']} retrieval: {score["retrieval"]} passed: {score['passed']}")

# ==========================================================================================


async def main() -> None:
    cases = json.loads(DATASET.read_text())["cases"]
    agent = build_agent()

    async with agent:  # opens the toolset once (spawns + handshakes mcp_server), reused across cases
        observations = []
        for case in cases:
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
    scored = [score_case(case, obs) for case, obs in zip(cases, observations)]
    report(scored)


if __name__ == "__main__":
    asyncio.run(main())
