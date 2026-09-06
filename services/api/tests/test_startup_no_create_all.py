from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from app.db.session import Base, engine
from app.main import app


def test_app_startup_does_not_call_create_all(monkeypatch):
    """Verifies that importing or starting FastAPI lifespan context manager does NOT call Base.metadata.create_all."""
    mock_create_all = MagicMock()
    monkeypatch.setattr(Base.metadata, "create_all", mock_create_all)

    # Trigger FastAPI lifespan context via TestClient
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200

    # Assert Base.metadata.create_all was NEVER called during app startup
    mock_create_all.assert_not_called()
