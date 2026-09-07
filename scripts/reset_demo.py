import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "api"))

from app.core.config import settings
from app.models.domain import Base
from sqlalchemy import create_engine


def is_disposable_database(db_url: str) -> bool:
    url_lower = db_url.lower()
    return any(marker in url_lower for marker in ("demo", "disposable", "test", "seed", "tmp"))


def reset_demo_data() -> None:
    allow_seed = os.environ.get("ALLOW_DEMO_SEED", "").lower() in ("true", "1", "yes")
    db_url = settings.DATABASE_URL

    print(f"Target Database URL: {db_url}")

    if not allow_seed:
        print("ERROR: Demo reset refused. ALLOW_DEMO_SEED=true is required in environment.")
        sys.exit(1)

    if not is_disposable_database(db_url):
        print("ERROR: Demo reset refused. Target database URL must be a positively identified disposable/demo database (containing 'demo', 'disposable', 'test', or 'tmp'). Never run reset against production or development databases.")
        sys.exit(1)

    print("Confirmed disposable database and ALLOW_DEMO_SEED=true. Resetting synthetic demo database...")
    engine = create_engine(db_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Disposable demo database reset complete.")


if __name__ == "__main__":
    reset_demo_data()
