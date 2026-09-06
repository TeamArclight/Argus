import json
from pathlib import Path
from app.main import app


def export_openapi():
    openapi_schema = app.openapi()
    output_path = Path(__file__).parent.parent / "candidate_openapi.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)
    print(f"Successfully generated candidate OpenAPI schema at: {output_path.resolve()}")


if __name__ == "__main__":
    export_openapi()
