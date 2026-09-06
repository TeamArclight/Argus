import ast
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from app.db.session import Base
from app.main import app


def test_app_startup_does_not_call_create_all(monkeypatch):
    """Verifies that starting FastAPI lifespan context manager does NOT call Base.metadata.create_all."""
    mock_create_all = MagicMock()
    monkeypatch.setattr(Base.metadata, "create_all", mock_create_all)

    # Trigger FastAPI lifespan context via TestClient
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200

    # Assert Base.metadata.create_all was NEVER called during app startup
    mock_create_all.assert_not_called()


def test_source_code_has_no_create_all_calls():
    """AST regression test verifying that app/main.py source contains no create_all calls or references."""
    main_py_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
    assert main_py_path.exists()

    source = main_py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(main_py_path))

    class CreateAllVisitor(ast.NodeVisitor):
        def __init__(self):
            self.found_create_all = False

        def visit_Attribute(self, node: ast.Attribute):
            if node.attr == "create_all":
                self.found_create_all = True
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name):
            if node.id == "create_all":
                self.found_create_all = True
            self.generic_visit(node)

    visitor = CreateAllVisitor()
    visitor.visit(tree)
    assert not visitor.found_create_all, "app/main.py contains forbidden 'create_all' call or reference!"

