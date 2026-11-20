import pytest

from app import db
from app.clock import clock


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset_clock():
    clock.reset()
    yield
    clock.reset()


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Keep tests deterministic and fast: agent uses canned fallbacks."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
