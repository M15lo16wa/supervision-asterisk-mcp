import pytest

from src.application.analyze_call_quality import AnalyzeCallQualityUseCase
from src.application.get_call_records import GetCallRecordsUseCase
from src.application.hangup_channel import HangupChannelUseCase
from src.application.list_active_channels import ListActiveChannelsUseCase
from src.application.list_extensions import ListExtensionsUseCase
from src.application.originate_call import OriginateCallUseCase
from src.application.start_channel_spy import StartChannelSpyUseCase
from src.application.transfer_call import TransferCallUseCase
from src.domain.entities import CallDetailRecord, SpyMode
from src.domain.exceptions import ChannelNotFound, HitlConfirmationDenied
from tests.fakes import sample_channel, sample_extension


async def test_list_channels_output_is_not_enveloped(gateway, sanitizer):
    """La sortie d'un outil garde des identifiants utilisables par l'appelant.

    L'enveloppe `[UNTRUSTED DATA]` est réservée au texte envoyé au LLM
    (llm_chat) : elle corromprait ici le nom de canal, impossible à
    réutiliser pour un hangup/redirect ultérieur.
    """
    gateway.channels = [sample_channel()]
    out = await ListActiveChannelsUseCase(gateway, sanitizer).execute()
    assert sanitizer.neutralized and out == sanitizer.neutralized[-1]
    assert not sanitizer.seen  # pas d'enveloppe sur la sortie d'outil
    assert out[0]["name"] == "PJSIP/1001-0001"
    assert "UNTRUSTED DATA" not in out[0]["name"]


async def test_list_extensions_filters_context(gateway, sanitizer):
    gateway.extensions = [sample_extension("1001"), sample_extension("1002")]
    out = await ListExtensionsUseCase(gateway, sanitizer).execute(context="internal")
    assert len(out) == 2


async def test_get_call_records_clamps_limit(gateway, sanitizer):
    gateway.cdr = [
        CallDetailRecord(f"u{i}", "1001", "1002", "internal", "1001", "c", "d",
                         "", "", "", 1, 1, "ANSWERED", "Dial")
        for i in range(50)
    ]
    out = await GetCallRecordsUseCase(gateway, sanitizer).execute(limit=10)
    assert len(out) == 10


async def test_analyze_quality_returns_mos(gateway, sanitizer):
    out = await AnalyzeCallQualityUseCase(gateway, sanitizer).execute("PJSIP/1001-1")
    assert 1.0 <= out["mos_estimate"] <= 4.5
    assert out["rating"] in {"excellent", "good", "fair", "poor", "bad"}


async def test_originate_requires_hitl_before_acting(gateway, sanitizer, hitl_denied):
    with pytest.raises(HitlConfirmationDenied):
        await OriginateCallUseCase(gateway, hitl_denied, sanitizer).execute(
            "PJSIP/1001", "internal", "1002", user="alice"
        )
    assert gateway.calls == []  # action never reached


async def test_originate_acts_after_confirmation(gateway, sanitizer, hitl_ok):
    await OriginateCallUseCase(gateway, hitl_ok, sanitizer).execute(
        "PJSIP/1001", "internal", "1002", user="alice"
    )
    assert gateway.calls[0][0] == "originate"
    assert hitl_ok.calls[0][0] == "originate_call"


async def test_hangup_and_transfer_are_hitl_gated(gateway, sanitizer, hitl_denied):
    with pytest.raises(HitlConfirmationDenied):
        await HangupChannelUseCase(gateway, hitl_denied, sanitizer).execute("c1", "bob")
    with pytest.raises(HitlConfirmationDenied):
        await TransferCallUseCase(gateway, hitl_denied, sanitizer).execute(
            "c1", "1099", "internal", "bob"
        )
    assert gateway.calls == []


async def test_spy_listen_is_not_hitl_gated(gateway, sanitizer, hitl_denied):
    # listen mode: no confirmation required, so a denying HITL is never consulted
    gateway.channels = [sample_channel("PJSIP/1001-1")]
    await StartChannelSpyUseCase(gateway, hitl_denied, sanitizer).execute(
        "PJSIP/1001-1", "PJSIP/1099", SpyMode.LISTEN, user="sup"
    )
    assert gateway.calls[-1][0] == "spy"
    assert hitl_denied.calls == []


async def test_spy_whisper_is_hitl_gated(gateway, sanitizer, hitl_denied):
    gateway.channels = [sample_channel("PJSIP/1001-1")]
    with pytest.raises(HitlConfirmationDenied):
        await StartChannelSpyUseCase(gateway, hitl_denied, sanitizer).execute(
            "PJSIP/1001-1", "PJSIP/1099", SpyMode.WHISPER, user="adm"
        )
    assert gateway.calls == []


async def test_spy_rejects_unknown_target_channel(gateway, sanitizer, hitl_ok):
    """Un canal inexistant ne doit pas être rapporté comme un succès."""
    gateway.channels = [sample_channel("PJSIP/1001-1")]
    with pytest.raises(ChannelNotFound):
        await StartChannelSpyUseCase(gateway, hitl_ok, sanitizer).execute(
            "PJSIP/inexistant-1", "PJSIP/1099", SpyMode.LISTEN, user="sup"
        )
    assert [c[0] for c in gateway.calls] != ["spy"]
    assert hitl_ok.calls == []  # pas de confirmation demandée pour rien
