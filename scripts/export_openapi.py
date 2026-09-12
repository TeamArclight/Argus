"""Export current FastAPI application OpenAPI schema into contracts/openapi.json.

Run from repository root:
    python scripts/export_openapi.py
"""
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
API_DIR = os.path.join(REPO_ROOT, "services", "api")
sys.path.insert(0, API_DIR)

# Set deterministic test secret so Settings import succeeds cleanly
os.environ.setdefault("ARGUS_JWT_SECRET", "test-secret-key-must-be-at-least-32-chars-long-001")


def main() -> None:
    try:
        from app.main import app
        schema = app.openapi()
        out_path = os.path.join(REPO_ROOT, "contracts", "openapi.json")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2)
            f.write("\n")
        print(f"Successfully exported FastAPI OpenAPI schema to {out_path} ({len(schema.get('paths', {}))} endpoints)")
    except Exception as err:
        print(f"ERROR exporting OpenAPI schema: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
