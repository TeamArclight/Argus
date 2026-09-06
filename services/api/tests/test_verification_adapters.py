import pytest
from app.verification.adapters import (
    BlacklistVerificationAdapter,
    EPFOVerificationAdapter,
    GSTVerificationAdapter,
    MCAVerificationAdapter,
    UdyamVerificationAdapter,
)
from app.schemas.canonical import VerificationStatus


@pytest.mark.asyncio
async def test_gst_adapter_success():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5", "bidder_name": "Acme Corp", "verification_mode": "demo"}, "general.gstin")
    assert res.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_gst_adapter_mismatch():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify({"id": "B1", "gstin": "27AAAAA0000A199", "bidder_name": "CREST LOGISTICS", "verification_mode": "demo"}, "general.gstin")
    assert res.status == VerificationStatus.MISMATCH


@pytest.mark.asyncio
async def test_gst_adapter_timeout():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5", "simulated_mode": "timeout"}, "general.gstin")
    assert res.status == VerificationStatus.TIMEOUT
    assert "timeout" in res.error_message.lower()


@pytest.mark.asyncio
async def test_udyam_adapter_unavailable():
    adapter = UdyamVerificationAdapter()
    res = await adapter.verify({"id": "B1", "udyam_number": "UDYAM-MH-01-0001234", "simulated_mode": "unavailable"}, "general.udyam")
    assert res.status == VerificationStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_blacklist_adapter_blacklisted():
    adapter = BlacklistVerificationAdapter()
    res = await adapter.verify({"id": "B1", "bidder_name": "Malicious Traders Ltd", "verification_mode": "demo"}, "debarment.status")
    assert res.status == VerificationStatus.MISMATCH
    assert res.verified_value["debarred"] is True
