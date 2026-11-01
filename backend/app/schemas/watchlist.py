"""Watchlist API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.research import Ticker


class WatchlistCreateRequest(BaseModel):
    ticker: Ticker


class WatchlistEntryResponse(BaseModel):
    cik: int
    ticker: str
    created_at: datetime


class WatchlistPageResponse(BaseModel):
    items: list[WatchlistEntryResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
