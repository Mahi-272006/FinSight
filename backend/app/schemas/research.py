"""Request and response schemas for synchronous research runs."""

from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

Ticker = Annotated[
    str,
    Field(
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,9}$",
        description="SEC ticker symbol.",
    ),
]

ResearchQuestion = Annotated[
    str,
    StringConstraints(min_length=1, max_length=4000, strip_whitespace=True),
]
ThreadId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, strip_whitespace=True),
]


class ResearchRequest(BaseModel):
    ticker: Ticker
    question: ResearchQuestion
    save: bool = True
    thread_id: ThreadId | None = None


class ResearchResponse(BaseModel):
    """All fields from ResearchResult plus server thread and save intent."""

    question: str
    ticker: str
    company: dict[str, Any] | None
    final_answer: str
    citations: list[str]
    limitations: list[str]
    trace: list[str]
    validation: dict[str, Any] | None
    iterations: int
    revises: int
    tool_calls: list[dict[str, Any]]
    thread_id: str
    save_requested: bool
