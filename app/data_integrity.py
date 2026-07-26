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


#: The environment variable an operator sets, named in the banner so the sentence is actionable
#: by the person who can act on it. Documented in README.md and passed through docker-compose.yml.
ENFORCEMENT_SETTING = "ENFORCE_FOREIGN_KEYS"

NOTHING_CHANGED = "Nothing has been changed or removed."

ORPHAN_REMEDY = "Open each listed record and choose the correct link on its own page."

SWITCHED_OFF_BY_SETTING = (
    f"The Hub's link checking is switched off by the {ENFORCEMENT_SETTING} setting, so records are "
    "not being checked for links to records that are no longer in the Hub."
)

SWITCH_BACK_ON = (
    f"Setting {ENFORCEMENT_SETTING} back to true and restarting the Hub switches link checking "
    "back on."
)


def orphan_count_sentence(orphans: tuple[OrphanRecord, ...] | list[OrphanRecord]) -> str:
    count = len(orphans)
    noun = "record points" if count == 1 else "records point"
    return f"{count} {noun} at a record that is no longer in the Hub."


def orphan_warning_text(orphans: tuple[OrphanRecord, ...] | list[OrphanRecord]) -> str | None:
    """Plain operational wording for the banner. Not a tax, accounting, or eligibility statement."""
    if not orphans:
        return None
    count = len(orphans)
    noun = "record points" if count == 1 else "records point"
    return (
        f"{count} {noun} at a record that is no longer in the Hub, so link checking is switched off "
        f"until that is resolved. {NOTHING_CHANGED} {ORPHAN_REMEDY}"
    )


def integrity_warning_text(
    orphans: tuple[OrphanRecord, ...] | list[OrphanRecord],
    *,
    disabled_by_setting: bool,
) -> str | None:
    """The banner sentence for whichever reason link checking is not active, or None if it is.

    ADR-0005 D3.6 requires the same banner when an operator has switched enforcement off, and that
    reason is invisible in the records: on a clean database ``orphan_warning_text`` returns None,
    so publishing its result alone left an operator-disabled workspace showing no banner at all --
    the one state in which the operator most needs to be told, because they may have set the
    variable weeks ago in a shell they no longer have open.

    The two reasons are independent and can hold together, so both are reported. The wording stays
    operational: the Hub has found a condition and is telling the operator what is not happening
    and what to do about it. It claims no repair (ADR-0005 D3.5, which forbids one), passes no
    verdict on the records (ADR-0002 line 59), and promises nothing about the future.
    """
    if not disabled_by_setting:
        return orphan_warning_text(orphans)

    sentences = [SWITCHED_OFF_BY_SETTING]
    if orphans:
        sentences.append(orphan_count_sentence(orphans))
    sentences.append(NOTHING_CHANGED)
    if orphans:
        sentences.append(ORPHAN_REMEDY)
    sentences.append(SWITCH_BACK_ON)
    return " ".join(sentences)


def apply_foreign_key_policy(session: Session, target_engine: Engine | None = None) -> IntegrityScanResult:
    """Scan for orphans, then either enable link enforcement or withhold it and report.

    ADR-0005 D3.3, in order: the scan is a read taken while enforcement is still off, so it can
    never fail on the condition it is looking for. A clean scan enables the pragma and disposes the
    pool. A scan that finds orphans leaves the pragma off, logs each orphan, and stores the report:
    withholding the *new* control is the only option that is neither destructive nor silent, and
    refusing to start would strand the operator with a database they cannot inspect.

    D3.6's escape hatch is the second reason enforcement can be inactive, and it is the reason the
    published warning is not simply the orphan report: an operator who has set ENFORCE_FOREIGN_KEYS
    to false on a clean database is running without link checking and no scan will ever say so.
    The warning states whichever reasons hold, so the banner appears in both cases and in the case
    where both apply at once.
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

    disabled_by_setting = not get_settings().enforce_foreign_keys

    # Logged before the branch, so an orphan is reported whichever reason enforcement is withheld
    # for. Reaching this with the setting off used to return early and log nothing about them.
    for orphan in orphans:
        logger.warning(
            "%s %s (id %s) points at %s id %s, which no longer exists.",
            orphan.child_dataset,
            orphan.child_display,
            orphan.child_id,
            orphan.parent_dataset,
            orphan.missing_parent_id,
        )

    if disabled_by_setting or orphans:
        database.set_fk_enforcement(False, active)
        if disabled_by_setting:
            logger.warning(
                "Link enforcement is switched off by %s. Records can be saved pointing at records "
                "that are no longer in the Hub until it is set back to true and the Hub restarted.",
                ENFORCEMENT_SETTING,
            )
        INTEGRITY_REPORT = orphans
        INTEGRITY_WARNING = integrity_warning_text(orphans, disabled_by_setting=disabled_by_setting)
        return IntegrityScanResult(orphans, False, missing, INTEGRITY_WARNING)

    database.set_fk_enforcement(True, active)
    INTEGRITY_REPORT = ()
    INTEGRITY_WARNING = None
    return IntegrityScanResult((), True, missing, None)
