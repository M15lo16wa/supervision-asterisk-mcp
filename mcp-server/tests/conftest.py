import sys
from pathlib import Path

import pytest

# le paquet `src` est à la racine de mcp-server
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fakes import (
    FakeAsteriskGateway,
    PassthroughSanitizer,
    RecordingHitl,
)


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    """Isole le journal d'audit dans un fichier temporaire par test."""
    import src.audit.journal as journal

    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(journal, "_audit_logger", None)
    yield
    monkeypatch.setattr(journal, "_audit_logger", None)


@pytest.fixture
def gateway():
    return FakeAsteriskGateway()


@pytest.fixture
def sanitizer():
    return PassthroughSanitizer()


@pytest.fixture
def hitl_ok():
    return RecordingHitl(approve=True)


@pytest.fixture
def hitl_denied():
    return RecordingHitl(approve=False)
