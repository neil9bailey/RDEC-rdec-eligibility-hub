"""Link integrity: the orphan pre-scan and the foreign-key enforcement policy.

ADR-0005 D2 and D3, relocated here from ``app/database.py`` by Ruling R1 (2026-07-26). The names,
signatures and behaviour are unchanged by the move.

The layering is the point of the ruling. This module reads the schema and the operator's records
and decides an operational policy; ``app/database.py`` owns the engine, the session and the pragma
listener, and knows nothing about datasets. That direction lets ``app.data_management`` be imported
at module level here -- it is the single source of truth for which columns are foreign keys -- so an
``ImportError`` surfaces at process start rather than at scan time.

``app/database.py`` deliberately does not import these names back. A compatibility re-export would
reinstate the dependency inversion the ruling removed.
"""

from dataclasses import dataclass
from typing import Any
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app import database
from app.data_management import DATASET_BY_KEY, DATASETS
from app.settings import get_settings


logger = logging.getLogger(__name__)


DISPLAY_FIELD_ORDER = (
    "company_name",
    "customer_name",
    "contract_name",
    "solution_name",
    "project_title",
    "name",
    "label",
    "activity",
    "activity_name",
    "professional_name",
    "source_reference",
    "summary",
)


@dataclass(frozen=True)
class OrphanRecord:
    child_dataset: str
    child_id: int
    child_display: str
    field: str
    parent_dataset: str
    missing_parent_id: int


@dataclass(frozen=True)
class IntegrityScanResult:
    orphans: tuple[OrphanRecord, ...]
    enforcement_enabled: bool
    missing_constraints: dict[str, tuple[str, ...]]
    warning: str | None


# Set by apply_foreign_key_policy() at startup and read by the page renderer (ADR-0005 D3.3 step 4).
INTEGRITY_REPORT: tuple[OrphanRecord, ...] = ()
INTEGRITY_WARNING: str | None = None


def missing_foreign_key_constraints(target_engine: Engine | None = None) -> dict[str, tuple[str, ...]]:
    """Columns the models declare as foreign keys that the live schema does not enforce.

    SQLite cannot attach a REFERENCES clause through ALTER TABLE ... ADD COLUMN, so a column added
    by apply_sqlite_schema_updates() has no constraint in the DDL, permanently, while the same
    column on a freshly created database has one. Enforcement therefore differs between fresh and
    upgraded databases, and the pragma is a no-op for anything listed here (ADR-0005 D2).
    """
    active = target_engine if target_engine is not None else database.engine
    if active.dialect.name != "sqlite":
        return {}
    missing: dict[str, tuple[str, ...]] = {}
    with active.connect() as connection:
        present = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        for table_name, table in SQLModel.metadata.tables.items():
            declared = sorted({column.name for column in table.columns if column.foreign_keys})
            if not declared or table_name not in present:
                continue
            enforced = {
                row[3] for row in connection.execute(text(f"PRAGMA foreign_key_list({table_name})"))
            }
            absent = tuple(name for name in declared if name not in enforced)
            if absent:
                missing[table_name] = absent
    return missing


def _display_for(spec: Any, record: Any) -> str:
    for field in DISPLAY_FIELD_ORDER:
        value = getattr(record, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for field in spec.natural_key:
        value = getattr(record, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{spec.label} #{getattr(record, 'id', 0) or 0}"


def scan_orphans(session: Session) -> list[OrphanRecord]:
    """Report records whose parent identifier does not match an existing record.

    Read-only. It never modifies, moves, quarantines, or deletes anything (ADR-0005 D3.5): these
    are the sponsor's RDEC evidence records, and relocating them would be a destructive, non-obvious
    mutation. The relationships come from DatasetSpec.foreign_keys, the same single source of truth
    the import path uses, so the two consumers cannot drift apart (D3.2).
    """
    orphans: list[OrphanRecord] = []
    parent_ids: dict[str, set[int]] = {}
    for spec in DATASETS:
        if not spec.foreign_keys:
            continue
        records = list(session.exec(select(spec.model)))
        if not records:
            continue
        for field, parent_key in spec.foreign_keys:
            parent_spec = DATASET_BY_KEY[parent_key]
            if parent_key not in parent_ids:
                parent_ids[parent_key] = {
                    int(parent.id)
                    for parent in session.exec(select(parent_spec.model))
                    if parent.id is not None
                }
            known = parent_ids[parent_key]
            for record in records:
                value = getattr(record, field, None)
                if value is None:
                    continue
                if int(value) in known:
                    continue
                orphans.append(
                    OrphanRecord(
                        child_dataset=spec.key,
                        child_id=int(getattr(record, "id", 0) or 0),
                        child_display=_display_for(spec, record),
                        field=field,
                        parent_dataset=parent_key,
                        missing_parent_id=int(value),
                    )
                )
    return orphans


def orphan_warning_text(orphans: tuple[OrphanRecord, ...] | list[OrphanRecord]) -> str | None:
    """Plain operational wording for the banner. Not a tax, accounting, or eligibility statement."""
    if not orphans:
        return None
    count = len(orphans)
    noun = "record points" if count == 1 else "records point"
    return (
        f"{count} {noun} at a record that is no longer in the Hub, so link checking is switched off "
        "until that is resolved. Nothing has been changed or removed. Open each listed record and "
        "choose the correct link on its own page."
    )


def apply_foreign_key_policy(session: Session, target_engine: Engine | None = None) -> IntegrityScanResult:
    """Scan for orphans, then either enable link enforcement or withhold it and report.

    ADR-0005 D3.3, in order: the scan is a read taken while enforcement is still off, so it can
    never fail on the condition it is looking for. A clean scan enables the pragma and disposes the
    pool. A scan that finds orphans leaves the pragma off, logs each orphan, and stores the report:
    withholding the *new* control is the only option that is neither destructive nor silent, and
    refusing to start would strand the operator with a database they cannot inspect.
    """
    global INTEGRITY_REPORT, INTEGRITY_WARNING

    active = target_engine if target_engine is not None else database.engine
    orphans = tuple(scan_orphans(session))
    missing = missing_foreign_key_constraints(active)

    if missing:
        logger.warning(
            "Link enforcement is partial: %s have no constraint in this database's schema.",
            ", ".join(f"{table}.{column}" for table, columns in missing.items() for column in columns),
        )

    if not get_settings().enforce_foreign_keys:
        database.set_fk_enforcement(False, active)
        INTEGRITY_REPORT = orphans
        INTEGRITY_WARNING = orphan_warning_text(orphans)
        logger.warning("Link enforcement is switched off by ENFORCE_FOREIGN_KEYS.")
        return IntegrityScanResult(orphans, False, missing, INTEGRITY_WARNING)

    if orphans:
        database.set_fk_enforcement(False, active)
        for orphan in orphans:
            logger.warning(
                "%s %s (id %s) points at %s id %s, which no longer exists.",
                orphan.child_dataset,
                orphan.child_display,
                orphan.child_id,
                orphan.parent_dataset,
                orphan.missing_parent_id,
            )
        INTEGRITY_REPORT = orphans
        INTEGRITY_WARNING = orphan_warning_text(orphans)
        return IntegrityScanResult(orphans, False, missing, INTEGRITY_WARNING)

    database.set_fk_enforcement(True, active)
    INTEGRITY_REPORT = ()
    INTEGRITY_WARNING = None
    return IntegrityScanResult((), True, missing, None)
