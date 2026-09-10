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
