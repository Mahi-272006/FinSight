"""Synchronous research execution with one process-wide concurrency gate."""

import threading
import uuid
from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_research_agent_factory
from app.graph.agent import ResearchAgent
from app.schemas.research import ResearchRequest, ResearchResponse

router = APIRouter(prefix="/research", tags=["research"])

# ResearchAgent uses a process-local InMemorySaver by default, and the existing
# free-tier providers have shared TPM limits. Serialize only agent creation and
# synchronous graph execution; later persistence work belongs outside this lock.
_RESEARCH_EXECUTION_LOCK = threading.Lock()


@router.post("", response_model=ResearchResponse)
def run_research(
    payload: ResearchRequest,
    agent_factory: Callable[[], ResearchAgent] = Depends(get_research_agent_factory),
):
    """Run the existing ResearchAgent and return its actual structured result."""
    thread_id = payload.thread_id or str(uuid.uuid4())
    with _RESEARCH_EXECUTION_LOCK:
        agent: ResearchAgent = agent_factory()
        result = agent.run(
            payload.question,
            payload.ticker.upper(),
            thread_id=thread_id,
        )

    if result.company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    # Report persistence is introduced in Stage 7. Keep the requested flag
    # explicit in the response without pretending this Stage 6 endpoint saved
    # a durable report.
    return ResearchResponse(
        **asdict(result),
        thread_id=thread_id,
        save_requested=payload.save,
    )
