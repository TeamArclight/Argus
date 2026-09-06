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
    res = await adapter.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5", "bidder_name": "Acme Corp"}, "general.gstin")
    assert res.status == VerificationStatus.VERIFIED
    assert res.verified_value["gstin"] == "27AAAAA0000A1Z5"


@pytest.mark.asyncio
async def test_gst_adapter_mismatch():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify({"id": "B1", "gstin": "27AAAAA0000A199"}, "general.gstin")
    assert res.status == VerificationStatus.MISMATCH
    assert res.error_message is not None


@pytest.mark.asyncio
async def test_gst_adapter_timeout():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5", "simulated_mode": "timeout"}, "general.gstin")
    assert res.status == VerificationStatus.TIMEOUT
    assert "timed out" in res.error_message


@pytest.mark.asyncio
async def test_udyam_adapter_unavailable():
    adapter = UdyamVerificationAdapter()
    res = await adapter.verify({"id": "B1", "udyam_number": "UDYAM-MH-01-0001234", "simulated_mode": "unavailable"}, "general.udyam")
    assert res.status == VerificationStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_blacklist_adapter_blacklisted():
    adapter = BlacklistVerificationAdapter()
    res = await adapter.verify({"id": "B1", "bidder_name": "Malicious Traders Ltd"}, "debarment.status")
    assert res.status == VerificationStatus.MISMATCH
    assert res.verified_value["blacklisted"] is True
