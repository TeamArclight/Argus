import sys
from pathlib import Path
from alembic.config import Config
from alembic import command

def run_migrations() -> None:
    """Executes Alembic migrations to upgrade the database schema to head."""
    api_dir = Path(__file__).resolve().parent.parent
    ini_path = api_dir / "alembic.ini"
    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(api_dir / "alembic"))
    print("Running Alembic database migrations (upgrade head)...")
    command.upgrade(alembic_cfg, "head")
    print("Migrations complete.")

if __name__ == "__main__":
    run_migrations()
