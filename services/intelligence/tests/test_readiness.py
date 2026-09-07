from argus_ai.readiness import readiness

def test_readiness_never_exposes_configuration_secrets(monkeypatch):
    monkeypatch.setenv("ARGUS_MODEL_PROVIDER", "gemini")
    monkeypatch.setenv("ARGUS_GEMINI_API_KEY", "must-not-appear")
    result = readiness()
    assert result["checks"]["model"]["ready"] is True
    assert "must-not-appear" not in str(result)
