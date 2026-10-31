"""M7 demo: investment thesis monitoring (two-layer assessment).

    python scripts/thesis.py create --ticker MSFT --horizon FY2026 \
        --statement "Microsoft sustains double-digit growth through FY2026." \
        --condition "revenue_growth_yoy>=0.10" --condition "operating_income_margin>=0.40" \
        --fiscal-year 2026
    python scripts/thesis.py assess <id>        # verified layer + best-effort interpretation
    python scripts/thesis.py show <id>          # thesis + conditions + append-only history
    python scripts/thesis.py list

The create command snapshots each condition's current value (evidence at
creation); assess evaluates conditions against the M2 engine's numbers and
appends an immutable history row. Interpretation via the LLM chain is
best-effort: if every provider fails, the assessment still saves with the
deterministic explanation (the verified layer never depends on the LLM).

Exit codes: 0 = success; 1 = unknown ticker/thesis or invalid input;
2 = LLM chain unconfigured (only matters when interpretation was requested
    and no provider is available at all).
"""

import argparse
import sys
from pathlib import Path

# Windows consoles default to cp1252; model-generated text contains Unicode
# that cp1252 cannot encode. UTF-8 + replace: garble glyphs, never the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.database.postgres import get_session
from app.services.llm_manager import build_manager
from app.services.llm_providers import LLMConfigurationError, LLMError
from app.services.thesis_service import (
    ThesisInputError,
    ThesisNotFoundError,
    ThesisService,
)
from app.utils.logging import configure_logging


def _fmt_value(value) -> str:
    if value is None:
        return "n/a"
    if abs(value) < 10:
        return f"{value:.4f}"
    return f"{value:,.0f}"


def _fmt_period(fiscal_year, fiscal_period) -> str:
    """'FY2026' for annual rows, 'Q1 FY2026' for quarters."""
    if fiscal_year is None:
        return "unknown period"
    if fiscal_period in (None, "FY"):
        return f"FY{fiscal_year}"
    return f"{fiscal_period} FY{fiscal_year}"


def cmd_create(args, service: ThesisService) -> int:
    thesis_id = service.create_thesis(
        ticker=args.ticker,
        statement=args.statement,
        horizon=args.horizon,
        condition_texts=args.condition,
        fiscal_year=args.fiscal_year,
        fiscal_period=args.fiscal_period,
    )
    data = service.get_thesis(thesis_id)
    print(f"Thesis #{data['id']} created for {data['ticker']} (horizon {data['horizon']}).")
    print(f"  Statement: {data['statement']}")
    print("  Conditions (with evidence captured NOW, at thesis creation):")
    for cond in data["conditions"]:
        snap = cond["creation_evidence"]
        if snap.get("value") is None:
            captured = "no data yet for this metric/period"
        else:
            captured = (
                f"{_fmt_value(snap['value'])} ({snap['tier']}, {snap['period']}"
                + (f", formula: {snap['formula']}" if snap.get("formula") else "")
                + ")"
            )
        print(f"    [{cond['id']}] {cond['description']}")
        print(f"        snapshot: {captured}")
    print(f"\nNext: python scripts/thesis.py assess {data['id']}")
    return 0


def cmd_assess(args, service_factory) -> int:
    # Build the LLM manager lazily here: show/list/create work without keys,
    # and assess degrades to deterministic explanations if the chain is
    # empty -- only an explicitly-forced interpretation needs config.
    manager = None
    if not args.no_interpret:
        try:
            manager = build_manager(get_settings())
        except LLMConfigurationError as exc:
            if args.require_interpret:
                print(f"Configuration: {exc}")
                return 2
            print(f"Note: no LLM configured ({exc}); using deterministic explanations.\n")
    service = service_factory(manager)
    result = service.assess(args.thesis_id, interpret=not args.no_interpret)

    print(f"Assessment #{result.assessment_id} for thesis #{result.thesis_id}")
    if result.status_changed_from:
        print(f"  Status: {result.status} (changed from {result.status_changed_from})")
    else:
        print(f"  Status: {result.status}")

    print("  Conditions:")
    for e in result.evaluations:
        marker = {"met": "[met]    ", "not_met": "[NOT MET]", "incomplete": "[???]    "}
        print(f"    {marker[e.verdict]} {e.description}")
        if e.verdict == "incomplete":
            print(f"              reason: {e.reason}")
        else:
            provenance = e.tier
            if e.formula:
                provenance += f", formula: {e.formula}"
            if e.accessions:
                provenance += f", source: {', '.join(e.accessions)}"
            print(
                f"              evaluated {_fmt_value(e.value)} at "
                f"{_fmt_period(e.fiscal_year, e.fiscal_period)} ({provenance})"
            )

    if result.signals_context:
        print(f"  Investigation signals (context only, never verdicts): {len(result.signals_context)}")
        for s in result.signals_context:
            print(f"    - {s['signal_type']} ({s['severity']}, {s['period']}): {s['headline']}")

    print(f"\n  Explanation ({result.explanation_source}):")
    print(f"    {result.explanation}")
    if manager is not None:
        manager.close()
    return 0


def cmd_show(args, service: ThesisService) -> int:
    data = service.get_thesis(args.thesis_id)
    print(f"Thesis #{data['id']} -- {data['ticker']} (horizon {data['horizon']})")
    print(f"  Statement: {data['statement']}")
    print(f"  Current status: {data['status']}")
    print(f"  Created: {data['created_at']}")
    print("  Conditions (immutable after creation):")
    for cond in data["conditions"]:
        print(f"    [{cond['id']}] {cond['description']}")
    print(f"  Assessment history (append-only, {len(data['assessments'])} row(s)):")
    for a in data["assessments"]:
        change = f" (changed from {a['status_changed_from']})" if a["status_changed_from"] else ""
        print(f"    [{a['id']}] {a['assessed_at']} -> {a['status']}{change}")
        print(f"        explanation ({a['explanation_source']}): {a['explanation']}")
    return 0


def cmd_list(args, service: ThesisService) -> int:
    theses = service.list_theses()
    if not theses:
        print("No theses yet. Create one with scripts/thesis.py create.")
        return 0
    for t in theses:
        print(f"[{t['id']}] {t['ticker']} ({t['horizon']}) -- {t['status']}")
        print(f"     {t['statement']}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Investment thesis monitoring (M7)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a thesis with measurable conditions")
    p_create.add_argument("--ticker", required=True)
    p_create.add_argument("--statement", required=True)
    p_create.add_argument("--horizon", required=True, help='e.g. "FY2026" or "12 months"')
    p_create.add_argument("--condition", action="append", required=True,
                          help='metric OP threshold, e.g. "revenue_growth_yoy>=0.10" (repeatable)')
    p_create.add_argument("--fiscal-year", type=int, default=None,
                          help="Pin conditions to this fiscal year (default: latest period)")
    p_create.add_argument("--fiscal-period", default=None,
                          choices=["FY", "Q1", "Q2", "Q3", "Q4"],
                          help="Pin conditions to this period within the year (default: FY)")

    p_assess = sub.add_parser("assess", help="Run the two-layer assessment")
    p_assess.add_argument("thesis_id", type=int)
    p_assess.add_argument("--no-interpret", action="store_true",
                          help="Skip the LLM interpretation (verified layer only)")
    p_assess.add_argument("--require-interpret", action="store_true",
                          help="Exit 2 instead of degrading when no LLM is configured")

    p_show = sub.add_parser("show", help="Thesis + conditions + assessment history")
    p_show.add_argument("thesis_id", type=int)

    sub.add_parser("list", help="List theses")

    args = parser.parse_args(argv)
    configure_logging("WARNING")

    def service_factory(manager=None):
        return ThesisService(get_session, manager=manager)

    service = service_factory()
    try:
        if args.command == "create":
            return cmd_create(args, service)
        if args.command == "assess":
            return cmd_assess(args, service_factory)
        if args.command == "show":
            return cmd_show(args, service)
        if args.command == "list":
            return cmd_list(args, service)
    except (ThesisInputError, ThesisNotFoundError) as exc:
        print(f"Input error: {exc}")
        return 1
    except LLMError as exc:
        print(f"LLM failure: {exc}")
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
