import pytest

from src.domain.exceptions import UnauthorizedAction
from src.security.rbac import get_roles, has_role, require_role
from tests.fakes import FakeToken


def test_operateur_cannot_do_superviseur_or_admin():
    tok = FakeToken(["operateur"])
    assert has_role(tok, "operateur")
    assert not has_role(tok, "superviseur")
    assert not has_role(tok, "admin")
    with pytest.raises(UnauthorizedAction):
        require_role(tok, "admin")


def test_superviseur_inherits_operateur():
    tok = FakeToken(["superviseur"])
    assert has_role(tok, "operateur")
    assert has_role(tok, "superviseur")
    assert not has_role(tok, "admin")


def test_admin_inherits_everything():
    tok = FakeToken(["admin"])
    for role in ("operateur", "superviseur", "admin"):
        require_role(tok, role)  # no raise


def test_get_roles_handles_missing_claims():
    class Empty:
        claims = {}

    assert get_roles(Empty()) == set()
