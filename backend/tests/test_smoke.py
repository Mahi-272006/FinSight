"""Smoke test: prove the pytest harness itself works (M0).

These tests are deliberately trivial -- their job is to verify that:
1. pytest discovers tests in tests/ correctly
2. asyncio_mode = "auto" works (async tests run without decorators)
3. Importing the app package does not crash (catches missing __init__ files
   and import-order bugs early)

They do NOT test application logic; that starts in M1/M2.
"""

import sys
from pathlib import Path

# Ensure the backend root is importable when pytest runs from any directory.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_harness_works():
    """Sync test: the most basic possible assertion."""
    assert 1 + 1 == 2


async def test_async_harness_works():
    """Async test: verifies asyncio_mode = "auto" in pytest.ini is active.

    If this fails with "async def functions are not natively supported",
    the pytest-asyncio plugin or its configuration is broken -- and every
    async test in M5/M6 would fail too. Better to catch that now.
    """
    result = await _awaited_value()
    assert result == "ok"


async def _awaited_value() -> str:
    return "ok"


def test_app_package_imports():
    """Importing app should never raise, even with all modules empty."""
    import app  # noqa: F401

    assert app is not None
