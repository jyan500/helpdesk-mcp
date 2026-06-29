"""
Agent registry (Phase 4) — SCAFFOLD. Fill in the TODOs.

This is the "one place that knows about every specialist" the build plan asks for.
Each specialist is an `AgentConfig` (defined in agent/loop.py): a name, a system
prompt, the tool declarations the model sees, and the name->callable registry the
loop dispatches through. The orchestrator (agent/orchestrator.py) looks agents up
HERE by intent and hands them to `stream_agent`.

Why a separate module? It keeps the loop generic (it knows AgentConfig, not which
agents exist) and gives the orchestrator a single import. Adding Phase 5's Action
agent will mean adding one entry here — nothing in loop.py changes.

Note: import AgentConfig FROM loop.py (loop.py must not import this module back, or
you create a circular import — that's why the agents live here, not in loop.py).
"""
from __future__ import annotations

from agent.loop import (
    ACCOUNT_SYSTEM_PROMPT,
    ACTION_SYSTEM_PROMPT,
    KNOWLEDGE_SYSTEM_PROMPT,
    AgentConfig,
)
from tools.account import ACCOUNT_TOOL_DECLS
from tools.account import TOOLS as ACCOUNT_TOOLS
from tools.action import ACTION_TOOL_DECLS, REQUIRES_APPROVAL
from tools.action import TOOLS as ACTION_TOOLS
from tools.knowledge import KNOWLEDGE_TOOL_DECLS
from tools.knowledge import TOOLS as KNOWLEDGE_TOOLS

# Worked example — the Account specialist, assembled from pieces you already have.
# (Adjust the keyword names if you named your AgentConfig fields differently.)
ACCOUNT_AGENT = AgentConfig(
    name="account",
    system_prompt=ACCOUNT_SYSTEM_PROMPT,
    tool_decls=ACCOUNT_TOOL_DECLS,
    tools=ACCOUNT_TOOLS,
)

# TODO: build KNOWLEDGE_AGENT the same way, from the KNOWLEDGE_* pieces above.
KNOWLEDGE_AGENT = AgentConfig(
    name="knowledge", 
    system_prompt=KNOWLEDGE_SYSTEM_PROMPT, 
    tool_decls=KNOWLEDGE_TOOL_DECLS, 
    tools=KNOWLEDGE_TOOLS
)

# Phase 5 — the ACTION specialist. Note the COMPOSED registry: it gets the account
# read tools AND the action tools, so a single agent can resolve "alice's latest
# order" -> order id (via get_customer/get_orders) and THEN act on it (issue_refund).
# This is the registry pattern paying off — assembling a third agent is just merging
# dicts and lists; nothing in loop.py changed to support it.
#
# requires_approval is what makes the loop PAUSE on the irreversible tools — it's
# exactly tools.action.REQUIRES_APPROVAL ({"issue_refund", "send_email"}), with
# create_ticket deliberately left out so it runs immediately.
#
# TODO: build ACTION_AGENT from the pieces above:
#   ACTION_AGENT = AgentConfig(
#       name="action",
#       system_prompt=ACTION_SYSTEM_PROMPT,
#       tool_decls=ACCOUNT_TOOL_DECLS + ACTION_TOOL_DECLS,   # read + act decls
#       tools={**ACCOUNT_TOOLS, **ACTION_TOOLS},             # merged registry
#       requires_approval=REQUIRES_APPROVAL,
#   )
ACTION_AGENT = AgentConfig(
    name="action",
    system_prompt=ACTION_SYSTEM_PROMPT,
    tool_decls=ACCOUNT_TOOL_DECLS + ACTION_TOOL_DECLS, # requires the account info + action tools
    tools={**ACCOUNT_TOOLS, **ACTION_TOOLS},
    requires_approval=REQUIRES_APPROVAL
)


# AGENTS: the intent label the orchestrator produces -> the specialist to run.
# The KEYS here must match exactly the labels classify()/plan() can return (see
# orchestrator.py INTENTS).
#
# TODO: add "action": ACTION_AGENT alongside the two below (Phase 5 unstubs it; the
#   orchestrator no longer needs its "not available yet" message — that's subpart 6).
AGENTS: dict[str, AgentConfig] = {
    "account": ACCOUNT_AGENT,
    "knowledge": KNOWLEDGE_AGENT,
    "action": ACTION_AGENT,
}
