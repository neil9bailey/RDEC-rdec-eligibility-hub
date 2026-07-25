from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.rule_loader import clear_rule_file_cache, load_rule_file


REQUIRED_RULE_KEYS = {
    "eligibility_weights.yml": [
        "weights",
        "score_bands",
        "negative_advance_terms",
        "negative_uncertainty_terms",
    ],
    "blockers.yml": ["automatic_blockers"],
    "cost_categories.yml": ["categories", "validation_flags"],
    "claim_period_rules.yml": ["claim_notification", "aif"],
    "aif_rules.yml": ["project_selection"],
    "entitlement_rules.yml": ["statuses", "customer_type_defaults"],
}


@dataclass(frozen=True)
class Rules:
    eligibility: dict[str, Any]
    blockers: dict[str, Any]
    cost_categories: dict[str, Any]
    claim_period: dict[str, Any]
    aif: dict[str, Any]
    entitlement: dict[str, Any]

    def eligibility_weights(self) -> dict[str, int]:
        return self.eligibility["weights"]

    def score_bands(self) -> dict[str, dict[str, Any]]:
        return self.eligibility["score_bands"]

    def negative_advance_terms(self) -> list[str]:
        return list(self.eligibility.get("negative_advance_terms", []))

    def negative_uncertainty_terms(self) -> list[str]:
        return list(self.eligibility.get("negative_uncertainty_terms", []))

    def blocker_definitions(self) -> dict[str, str]:
        return {
            str(item["code"]): str(item["label"])
            for item in self.blockers.get("automatic_blockers", [])
        }

    def blocker_label(self, code: str) -> str:
        label = self.blocker_definitions().get(code, code.replace("_", " ").title())
        return label if label.endswith(".") else f"{label}."

    def review_flag_stop_phrases(self, kind: str) -> list[str]:
        """Optional stop phrases that suppress a negative-term review flag.

        Deliberately not in REQUIRED_RULE_KEYS: a missing, empty, or malformed
        key degrades to "no stop phrases" rather than preventing startup
        (ADR-0003 D6).
        """
        configured = self.eligibility.get("review_flag_stop_phrases")
        if not isinstance(configured, dict):
            return []
        phrases = configured.get(kind)
        if not isinstance(phrases, list):
            return []
        return [str(phrase) for phrase in phrases if str(phrase).strip()]

    def review_flag_definitions(self) -> dict[str, str]:
        definitions: dict[str, str] = {}
        configured = self.blockers.get("review_flags")
        if not isinstance(configured, list):
            return definitions
        for item in configured:
            if isinstance(item, dict) and item.get("code"):
                definitions[str(item["code"])] = str(item.get("label") or "")
        return definitions

    def review_flag_label(self, code: str) -> str:
        """Label for a review flag, falling back to a generated label if unconfigured."""
        label = self.review_flag_definitions().get(code) or code.replace("_", " ").title()
        return label if label.endswith(".") else f"{label}."

    def aif_selection_rules(self) -> dict[str, Any]:
        return self.aif["project_selection"]

    def cost_category_labels(self) -> dict[str, str]:
        return {
            key: str(value.get("label", key))
            for key, value in self.cost_categories.get("categories", {}).items()
        }

    def cost_validation_flag(self, code: str) -> str:
        return str(self.cost_categories.get("validation_flags", {}).get(code, code.replace("_", " ")))

    def claim_notification_months_after_period_end(self) -> int:
        return int(self.claim_period["claim_notification"]["period_ends_months_after_period_of_account_end"])

    def claim_notification_warning_days(self) -> int:
        return int(self.claim_period["claim_notification"]["warning_days_before_deadline"])

    def entitlement_statuses(self) -> dict[str, str]:
        return dict(self.entitlement.get("statuses", {}))

    def entitlement_status_label(self, status: str) -> str:
        return self.entitlement_statuses().get(status, status)

    def customer_type_defaults(self) -> dict[str, dict[str, str]]:
        return dict(self.entitlement.get("customer_type_defaults", {}))

    def default_corporation_tax_status(self, customer_type: str, fallback: str = "unknown") -> str:
        item = self.customer_type_defaults().get(customer_type, {})
        return str(item.get("corporation_tax_status") or fallback)


def validate_rule_file(name: str, data: dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_RULE_KEYS[name] if key not in data]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Rule file {name} is missing required key(s): {joined}")


@lru_cache
def get_rules() -> Rules:
    data = {name: load_rule_file(name) for name in REQUIRED_RULE_KEYS}
    for name, content in data.items():
        validate_rule_file(name, content)
    return Rules(
        eligibility=data["eligibility_weights.yml"],
        blockers=data["blockers.yml"],
        cost_categories=data["cost_categories.yml"],
        claim_period=data["claim_period_rules.yml"],
        aif=data["aif_rules.yml"],
        entitlement=data["entitlement_rules.yml"],
    )


def validate_all_rules() -> None:
    get_rules()


def clear_rules_cache() -> None:
    clear_rule_file_cache()
    get_rules.cache_clear()

