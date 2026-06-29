import asyncio
from dotenv import load_dotenv

# Load .env into os.environ BEFORE anything reads it (genai.Client() picks up
# GEMINI_API_KEY here). FastAPI/uvicorn does not auto-load .env.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse

from agent.loop import resume_agent
from agent.orchestrator import stream_orchestrator
from db.session import AsyncSessionLocal
from utils.client import LLMClient

app = FastAPI()
llm = LLMClient()

# Enable CORS for next.js development server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SSE Format strictly requires "data: <payload>\n\n"
# always separate consecutive messages with double newlines
# when using EventSourceResponse, this automates this for you
@app.get("/api/chat", response_class=EventSourceResponse)
async def chat_endpoint(message: str):
	async for delta in llm.stream_response(message):
		yield {"delta": delta}
	yield {"done": True}


# Phase 4: the ORCHESTRATOR over SSE — the single entry point the frontend should
# use. Same per-stream session lifetime and event contract as the specialist
# endpoints, plus the new {"type":"route", "intent":...} event. Internally it
# classifies the message and delegates to the right specialist, so one chat box
# now handles account + knowledge questions (and stubs action until Phase 5).
# The DB session is opened HERE with `async with` (not Depends) so its lifetime
# provably spans the whole stream: it opens before the first event and closes
# only when the generator is exhausted — no reliance on framework teardown timing
# for streamed bodies. We pass the live session into the agent so the agent itself
# stays decoupled/testable.
@app.get("/api/orchestrator/chat", response_class=EventSourceResponse)
async def orchestrator_chat_endpoint(message: str):
	async with AsyncSessionLocal() as session:
		async for event in stream_orchestrator(message, session):
			yield event


# Phase 5: RESUME a paused action. When the action agent hits an approval-gated tool
# it ends the /chat stream with an {"type":"approval", pending_id, ...} event; the UI
# shows Approve/Deny and then calls THIS endpoint with that pending_id + the decision.
# We don't re-classify or re-run the orchestrator — resume_agent loads the saved
# PendingAction by id, so it already knows which agent and which tool to finish.
# Same per-stream `async with` session lifetime as the chat endpoints above.
# `decision` is "approve" or "deny" (resume_agent treats anything != "approve" as deny).
@app.get("/api/orchestrator/resume", response_class=EventSourceResponse)
async def orchestrator_resume_endpoint(pending_id: str, decision: str):
	async with AsyncSessionLocal() as session:
		async for event in resume_agent(pending_id, decision, session):
			yield event
