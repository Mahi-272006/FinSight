"""Research tools: retrieval only, never computing.

The LLM gets exactly three powers: read filing text (search), read
engine-recorded numbers (facts), read anomaly signals (signals). None of
them computes anything -- financial figures come from the M2 engine with
provenance attached, which is how the no-arithmetic rule survives adding
tools at all: the model can now *fetch* numbers instead of *making* them.

Every tool takes its dependencies by injection (session factory, vector
store, embedder) so tests run against SQLite-in-memory and fakes, with no
network, no Chroma, no model download.

Output discipline: every line a tool returns is formatted so the
deterministic validation node can parse it back -- accessions and figures
are printed verbatim, chunk ids are labeled, and signals carry their
"not a verdict" disclaimer (M3's rule, enforced at the only place that
speaks about them).
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence

from langchain_core.tools import BaseTool, tool

from app.database.models import Company, FinancialFact, FinancialMetric, InvestigationSignal


class _VectorStoreLike(Protocol):
    def search(self, query_vector: Sequence[float], k: int = 5, where: dict | None = None): ...


class _EmbedderLike(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def make_search_filing_sections(store: _VectorStoreLike, embedder: _EmbedderLike) -> BaseTool:
    @tool
    def search_filing_sections(ticker: str, query: str, section: str = "", k: int = 4) -> str:
        """Semantic search over one company's ingested SEC filing text.

        Returns the most relevant excerpts, each labeled with its
        chunk_id (accession:chunk_index) and provenance (form, accession,
        section, filed date). Use for qualitative evidence: risk factors,
        MD&A statements, business description, legal proceedings. `section`
        is optional (e.g. risk_factors, mdna, financial_statements,
        business). Cite excerpts in your answer by their accession number
        or chunk_id -- only from excerpts you actually retrieved.
        """
        vector = embedder.embed([query])[0]
        where: dict = {"ticker": ticker.upper()}
        if section:
            where["section"] = section
        hits = store.search(vector, k=max(1, min(int(k), 8)), where=where)
        if not hits:
            scope = f" in section '{section}'" if section else ""
            return f"No filing excerpts matched query={query!r} for {ticker.upper()}{scope}."
        parts: list[str] = []
        for rank, hit in enumerate(hits, start=1):
            meta = hit.metadata
            parts.append(
                f"[{rank}] chunk_id: {hit.id} | {meta.get('ticker')} {meta.get('form')} "
                f"{meta.get('accession')} section:{meta.get('section')} "
                f"filed:{meta.get('filing_date')}\n{hit.text}"
            )
        return "\n\n".join(parts)

    return search_filing_sections


def make_get_financial_facts(session_factory: Callable) -> BaseTool:
    @tool
    def get_financial_facts(ticker: str, metric: str = "", fiscal_year: int = 0,
                            limit: int = 10) -> str:
        """Fetch engine-recorded financial figures for a company, with provenance.

        The ONLY source of numbers you may quote. Values are exactly as the
        deterministic engine recorded them: VERIFIED FACT rows come straight
        from filings (with form/accession/filed date); CALCULATED METRIC rows
        include their formula. `metric` (optional) is one of: revenue,
        gross_profit, operating_income, net_income, total_assets,
        total_liabilities, current_assets, current_liabilities,
        cash_and_equivalents, total_debt, accounts_receivable,
        operating_cash_flow, capital_expenditure. `fiscal_year` (optional)
        filters one fiscal year. Never compute from these values; quote them
        with their provenance.
        """
        ticker_up = ticker.upper()
        with session_factory() as session:
            company = (session.query(Company)
                       .filter(Company.ticker == ticker_up).first())
            if company is None:
                return (f"Unknown ticker {ticker_up!r}: no company record. "
                        f"Only ingested companies can be queried.")
            facts_q = session.query(FinancialFact).filter(FinancialFact.cik == company.cik)
            metrics_q = session.query(FinancialMetric).filter(FinancialMetric.cik == company.cik)
            if metric:
                facts_q = facts_q.filter(FinancialFact.metric == metric)
                metrics_q = metrics_q.filter(FinancialMetric.metric == metric)
            if fiscal_year:
                facts_q = facts_q.filter(FinancialFact.fiscal_year == fiscal_year)
                metrics_q = metrics_q.filter(FinancialMetric.fiscal_year == fiscal_year)
            facts = (facts_q.order_by(FinancialFact.fiscal_year.desc(),
                                      FinancialFact.fiscal_period, FinancialFact.metric)
                     .limit(limit).all())
            calculated = (metrics_q.order_by(FinancialMetric.fiscal_year.desc(),
                                             FinancialMetric.fiscal_period, FinancialMetric.metric)
                          .limit(limit).all())

        fy_note = f" FY{fiscal_year}" if fiscal_year else ""
        metric_note = f" metric={metric}" if metric else ""
        lines = [f"{company.name} (CIK {company.cik}):"]
        if not facts and not calculated:
            return lines[0] + f" no facts matched{metric_note}{fy_note}."
        for fact in facts:
            restated = (f" [RESTATED; original value was {fact.value_original:,.0f} {fact.unit}]"
                        if fact.is_restated else "")
            filed = fact.filed_at.date().isoformat() if fact.filed_at else "?"
            lines.append(
                f"VERIFIED FACT {fact.metric} FY{fact.fiscal_year} {fact.fiscal_period} "
                f"({fact.period_type}): {fact.value:,.0f} {fact.unit} "
                f"[{fact.form} {fact.accession_number} filed {filed}]{restated}"
            )
        for row in calculated:
            if row.value is None:
                continue  # not computable: absence is not a number
            lines.append(
                f"CALCULATED METRIC {row.metric} FY{row.fiscal_year} {row.fiscal_period}: "
                f"{row.value:,.2f} {row.unit} [formula: {row.formula}; "
                f"sources: {row.source_accessions_json}]"
            )
        return "\n".join(lines)

    return get_financial_facts


def make_get_investigation_signals(session_factory: Callable) -> BaseTool:
    @tool
    def get_investigation_signals(ticker: str) -> str:
        """Fetch recorded anomaly-detection signals for a company.

        Signals are INVESTIGATION PROMPTS produced by the platform's rule and
        ML layers -- never conclusions of misstatement or fraud. When you
        mention one, quote its headline and evidence and label it as a
        signal for analyst review.
        """
        ticker_up = ticker.upper()
        with session_factory() as session:
            rows = (session.query(InvestigationSignal)
                    .filter(InvestigationSignal.ticker == ticker_up)
                    .order_by(InvestigationSignal.fiscal_year.desc(),
                              InvestigationSignal.fiscal_period)
                    .all())
        if not rows:
            return f"No recorded investigation signals for {ticker_up}."
        lines = ["NOTE: these are investigation signals, not conclusions of misstatement or fraud."]
        for row in rows:
            lines.append(
                f"SIGNAL {row.signal_type} severity:{row.severity} "
                f"FY{row.fiscal_year} {row.fiscal_period}: {row.headline}\n"
                f"{row.explanation}\n"
                f"evidence: {row.evidence_json} | accessions: {row.source_accessions_json}"
            )
        return "\n".join(lines)

    return get_investigation_signals


def build_research_tools(
    session_factory: Callable,
    store: _VectorStoreLike,
    embedder: _EmbedderLike,
) -> list[BaseTool]:
    """The agent's complete toolset, in a stable order (trace readability)."""
    return [
        make_search_filing_sections(store, embedder),
        make_get_financial_facts(session_factory),
        make_get_investigation_signals(session_factory),
    ]
