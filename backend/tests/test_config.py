"""Tests for Settings validation (M5 adds path anchoring; M0 rules live here too).

The relative-path bug this guards against: .env.example ships
CHROMA_PERSIST_DIRECTORY=./data/chroma_db, and a cwd-relative resolution made
the same script see all 542 chunks from backend/ but an EMPTY store from the
repo root (plus a stray chroma.sqlite3 created outside backend/).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import BACKEND_ROOT, Settings


def make_settings(**overrides) -> Settings:
    kwargs = {"sec_user_agent": "FinSight Test Suite finsight-tests@noreply.local"}
    kwargs.update(overrides)
    return Settings(**kwargs)


class TestPathAnchor:
    def test_relative_chroma_path_anchored_at_backend_root(self):
        # The exact value .env.example ships.
        s = Settings(
            sec_user_agent="FinSight Test Suite finsight-tests@noreply.local",
            chroma_persist_directory=Path("./data/chroma_db"),
        )
        assert s.chroma_persist_directory == BACKEND_ROOT / "data" / "chroma_db"
        assert s.chroma_persist_directory.is_absolute()

    @pytest.mark.parametrize("field", [
        "data_raw_dir", "data_processed_dir", "data_filings_dir", "logs_dir",
    ])
    def test_other_data_dirs_anchored_too(self, field):
        s = make_settings(**{field: Path("relative/thing")})
        value = getattr(s, field)
        assert value.is_absolute()
        assert value == BACKEND_ROOT / "relative" / "thing"

    def test_absolute_override_untouched(self):
        # A user's absolute override must never be rewritten.
        target = Path("D:/somewhere/else/chroma")
        s = make_settings(chroma_persist_directory=target)
        assert s.chroma_persist_directory == target

    def test_resolution_is_cwd_independent(self, monkeypatch, tmp_path):
        # The regression itself: two different CWDs must see the same path.
        s1 = make_settings(chroma_persist_directory=Path("./data/chroma_db"))
        monkeypatch.chdir(tmp_path)
        s2 = make_settings(chroma_persist_directory=Path("./data/chroma_db"))
        assert s1.chroma_persist_directory == s2.chroma_persist_directory


class TestUserAgentValidator:
    def test_empty_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            make_settings(sec_user_agent="")

    def test_placeholder_email_rejected(self):
        with pytest.raises(ValidationError, match="placeholder"):
            make_settings(sec_user_agent="FinSight finsight@example.com")

    def test_local_test_address_accepted(self):
        # .local addresses pass (tests make no SEC request); example.com does
        # not, because it is the universal placeholder marker.
        s = make_settings(sec_user_agent="FinSight Tests t@noreply.local")
        assert "noreply.local" in s.sec_user_agent
