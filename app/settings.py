from functools import lru_cache
from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent
RULES_DIR = BASE_DIR / "rules"


class Settings:
    app_name: str = "R&D Claim Evidence Hub"
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/rdec_hub.db")
    seed_reference_data: bool = os.getenv("SEED_REFERENCE_DATA", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    seed_demo_data: bool = os.getenv("SEED_DEMO_DATA", "false").lower() in {
        "1",
        "true",
        "yes",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
