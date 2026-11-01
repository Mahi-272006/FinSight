"""Transaction-owning write operations for the company watchlist."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database.repositories import CompanyRepository, WatchlistRepository


class WatchlistCompanyNotFound(LookupError):
    pass


class WatchlistEntryAlreadyExists(LookupError):
    pass


class WatchlistEntryNotFound(LookupError):
    pass


@dataclass(frozen=True)
class WatchlistRecord:
    cik: int
    ticker: str
    created_at: datetime


class WatchlistService:
    """Own commits and rollbacks for watchlist mutations."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def add(self, ticker: str) -> WatchlistRecord:
        session = self._session_factory()
        try:
            company = CompanyRepository(session).get_by_ticker(ticker.upper())
            if company is None:
                raise WatchlistCompanyNotFound()
            repository = WatchlistRepository(session)
            if repository.get(company.cik) is not None:
                raise WatchlistEntryAlreadyExists()
            entry = repository.add(company.cik)
            session.commit()
            return WatchlistRecord(
                cik=company.cik,
                ticker=company.ticker,
                created_at=entry.created_at,
            )
        except (WatchlistCompanyNotFound, WatchlistEntryAlreadyExists):
            session.rollback()
            raise
        except IntegrityError as exc:
            session.rollback()
            # The primary-key constraint also resolves concurrent duplicate adds.
            raise WatchlistEntryAlreadyExists() from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def remove(self, ticker: str) -> None:
        session = self._session_factory()
        try:
            company = CompanyRepository(session).get_by_ticker(ticker.upper())
            if company is None:
                raise WatchlistEntryNotFound()
            repository = WatchlistRepository(session)
            entry = repository.get(company.cik)
            if entry is None:
                raise WatchlistEntryNotFound()
            repository.delete(entry)
            session.commit()
        except WatchlistEntryNotFound:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
