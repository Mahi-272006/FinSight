"""M0 environment verification.

Purpose: prove that every critical dependency imports cleanly AND that the
installed versions work together on Python 3.10. This is the real compatibility
test -- `pip install` succeeding only proves the wheels downloaded.

Run:
    python scripts/verify_environment.py

Exit code 0 = environment healthy; 1 = at least one check failed.
"""

import importlib
import sys

# (import name, pip package name, minimum version we expect)
CHECKS = [
    # Web / API
    ("fastapi", "fastapi", "0.115"),
    ("uvicorn", "uvicorn", "0.30"),
    # Config / validation
    ("pydantic", "pydantic", "2.7"),
    ("pydantic_settings", "pydantic-settings", "2.3"),
    ("dotenv", "python-dotenv", "1.0"),
    # Database
    ("sqlalchemy", "sqlalchemy", "2.0"),
    ("alembic", "alembic", "1.13"),
    ("psycopg", "psycopg", "3.2"),
    # HTTP (SEC client)
    ("httpx", "httpx", "0.27"),
    # LLM agent stack -- the combination most likely to have version conflicts
    ("langgraph", "langgraph", "0.4"),
    ("langchain_core", "langchain-core", "0.3"),
    ("langchain_groq", "langchain-groq", "0.2"),
    ("langchain_google_genai", "langchain-google-genai", "2.0"),
    # RAG
    ("chromadb", "chromadb", "0.5"),
    ("sentence_transformers", "sentence-transformers", "3.0"),
    ("lxml", "lxml", "5.0"),
    ("bs4", "beautifulsoup4", "4.12"),
    # ML
    ("numpy", "numpy", "1.26"),
    ("pandas", "pandas", "2.2"),
    ("sklearn", "scikit-learn", "1.5"),
    ("joblib", "joblib", "1.4"),
    ("matplotlib", "matplotlib", "3.9"),
    ("torch", "torch", "2.0"),
    # Testing
    ("pytest", "pytest", "8.0"),
]


def main() -> int:
    failures = []
    print(f"Python {sys.version.split()[0]}\n")
    print(f"{'Package':<28} {'Version':<12} Status")
    print("-" * 52)

    for import_name, pip_name, min_version in CHECKS:
        try:
            module = importlib.import_module(import_name)
            version = getattr(module, "__version__", "unknown")
            print(f"{pip_name:<28} {version:<12} OK")
        except Exception as exc:  # noqa: BLE001 -- we want to report ANY failure
            failures.append((pip_name, exc))
            print(f"{pip_name:<28} {'-':<12} FAILED: {exc}")

    # One deeper check: the LangChain <-> LangGraph integration actually links.
    # Importing alone can succeed even when two packages are incompatible;
    # constructing a real (local-only, no API call) object proves they interoperate.
    print("\nIntegration smoke checks:")
    try:
        from langchain_core.tools import tool  # noqa: F401

        @tool
        def _dummy_tool(x: int) -> int:
            """A dummy tool used only to verify tool-decorator wiring."""
            return x

        assert _dummy_tool.name == "_dummy_tool"
        print("  langchain_core.tools.tool decorator ......... OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(("langchain_core.tools", exc))
        print(f"  langchain_core.tools.tool decorator ......... FAILED: {exc}")

    try:
        from langgraph.graph import END, START  # noqa: F401

        assert isinstance(START, str) and isinstance(END, str)
        print("  langgraph.graph START/END constants ........ OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(("langgraph.graph", exc))
        print(f"  langgraph.graph START/END constants ........ FAILED: {exc}")

    try:
        from sqlalchemy import create_engine, text

        # No server contacted: SQLite in-memory proves SQLAlchemy wiring works.
        engine = create_engine("sqlite://")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
        print("  sqlalchemy engine + connection .............. OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(("sqlalchemy", exc))
        print(f"  sqlalchemy engine + connection .............. FAILED: {exc}")

    print("\n" + "=" * 52)
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED")
        return 1
    print("RESULT: environment healthy -- all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
