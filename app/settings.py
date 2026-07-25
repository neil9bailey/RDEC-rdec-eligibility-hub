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
    data_import_enabled: bool = os.getenv("DATA_IMPORT_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    data_export_enabled: bool = os.getenv("DATA_EXPORT_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    data_cleanup_enabled: bool = os.getenv("DATA_CLEANUP_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    data_purge_enabled: bool = os.getenv("DATA_PURGE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    # ADR-0005 D3.6. Operator escape hatch for link checking. Enforcement defaults on, but is only
    # ever switched on after a clean orphan scan, so this turns off a control that may already be
    # withheld. Setting it false is logged loudly at startup.
    enforce_foreign_keys: bool = os.getenv("ENFORCE_FOREIGN_KEYS", "true").lower() in {
        "1",
        "true",
        "yes",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
