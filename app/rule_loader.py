from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.settings import RULES_DIR


@lru_cache
def load_rule_file(name: str) -> dict[str, Any]:
    path = Path(RULES_DIR) / name
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def rules_version_summary() -> dict[str, str]:
    summary: dict[str, str] = {}
    for path in sorted(Path(RULES_DIR).glob("*.yml")):
        data = load_rule_file(path.name)
        summary[path.name] = data.get("version", "unknown")
    return summary
