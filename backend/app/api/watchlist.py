"""Persistent watchlist API for supported companies."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_watchlist_service
from app.database.postgres import get_session
from app.database.repositories import WatchlistRepository
from app.schemas.watchlist import (
    WatchlistCreateRequest,
    WatchlistEntryResponse,
    WatchlistPageResponse,
)
from app.services.watchlist_service import (
    WatchlistCompanyNotFound,
    WatchlistEntryAlreadyExists,
    WatchlistEntryNotFound,
    WatchlistService,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

Ticker = Annotated[
    str,
    Path(
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,9}$",
        description="Supported SEC ticker symbol.",
    ),
]
PageLimit = Annotated[int, Query(ge=1, le=100, description="Maximum rows per page.")]
PageOffset = Annotated[int, Query(ge=0, description="Number of rows to skip.")]


@router.post("", response_model=WatchlistEntryResponse, status_code=status.HTTP_201_CREATED)
def add_watchlist_entry(
    payload: WatchlistCreateRequest,
    service: WatchlistService = Depends(get_watchlist_service),
):
    try:
        row = service.add(payload.ticker.upper())
    except WatchlistCompanyNotFound:
        raise HTTPException(status_code=404, detail="Company not found.")
    except WatchlistEntryAlreadyExists:
        raise HTTPException(status_code=409, detail="Company is already on the watchlist.")
    return WatchlistEntryResponse(
        cik=row.cik,
        ticker=row.ticker,
        created_at=row.created_at,
    )


@router.get("", response_model=WatchlistPageResponse)
def list_watchlist(
    session: Session = Depends(get_session),
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    repository = WatchlistRepository(session)
    rows = repository.list_page(limit, offset)
    return WatchlistPageResponse(
        items=[
            WatchlistEntryResponse(
                cik=row.cik,
                ticker=row.company.ticker,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=repository.count_all(),
        limit=limit,
        offset=offset,
    )


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist_entry(
    ticker: Ticker,
    service: WatchlistService = Depends(get_watchlist_service),
):
    try:
        service.remove(ticker.upper())
    except WatchlistEntryNotFound:
        raise HTTPException(status_code=404, detail="Watchlist entry not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
