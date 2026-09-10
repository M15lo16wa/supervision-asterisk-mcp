import pytest
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)

from src.adapters.hitl_confirmation import FastMcpHitlConfirmation
from src.domain.exceptions import HitlConfirmationDenied
from tests.fakes import FakeElicitContext


async def test_confirm_accepted_returns_true():
    ctx = FakeElicitContext(AcceptedElicitation(data=True))
    ok = await FastMcpHitlConfirmation(ctx).confirm("originate_call", "alice")
    assert ok is True


async def test_confirm_accepted_but_false_is_denied():
    ctx = FakeElicitContext(AcceptedElicitation(data=False))
    with pytest.raises(HitlConfirmationDenied):
        await FastMcpHitlConfirmation(ctx).confirm("originate_call", "alice")


async def test_confirm_declined_raises():
    ctx = FakeElicitContext(DeclinedElicitation())
    with pytest.raises(HitlConfirmationDenied):
        await FastMcpHitlConfirmation(ctx).confirm("hangup_channel", "bob")


async def test_confirm_cancelled_raises():
    ctx = FakeElicitContext(CancelledElicitation())
    with pytest.raises(HitlConfirmationDenied):
        await FastMcpHitlConfirmation(ctx).confirm("transfer_call", "bob")
