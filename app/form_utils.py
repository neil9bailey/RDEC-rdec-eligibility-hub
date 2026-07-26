from __future__ import annotations

from datetime import date
from typing import Any, Iterable
import html
import math

from fastapi.responses import HTMLResponse


# Any RDEC cost line above this is a data-entry error, not a real figure. The cap
# also keeps a sum of many thousands of lines a very long way from float overflow,
# which is what turned a stored 1e308 gross cost into an `inf` qualifying amount.
# Ratified at this value by ADR-0002 Ruling R6: IEEE-754 doubles are exact for
# integers to 2^53, so a figure in pence stays exact to roughly 9.0e13, and 1e12 is
# several orders of magnitude above any real RDEC cost line.
MAX_MONETARY_AMOUNT = 1_000_000_000_000.0

# A quantity bound for Hours and Days (ADR-0002 Ruling R6). One million hours is
# roughly 500 person-years on a single cost line; above that it is a typing error.
# Without it, hours and rates that are each individually acceptable multiply into a
# people-time gross that no monetary cap ever saw: 1e300 hours at the 1e12 rate cap
# stored `inf`, which is finding B1 re-entering through a second input path.
MAX_QUANTITY_AMOUNT = 1_000_000.0


def effective_maximum(maximum: float | None) -> float:
    """The bound a call actually gets. A per-call maximum may only tighten it.

    ADR-0002 Ruling R6: a control a caller can switch off is not a control. Before this,
    ``maximum=None`` meant *unbounded* -- fail-open by construction -- and a caller could
    also pass a ceiling above ``MAX_MONETARY_AMOUNT``. Neither is possible now: ``None``
    resolves to ``MAX_MONETARY_AMOUNT`` and anything else is intersected with it.
    """
    if maximum is None:
        return MAX_MONETARY_AMOUNT
    return min(maximum, MAX_MONETARY_AMOUNT)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _format_bound(bound: float) -> str:
    """Render a validation bound for a human, never in exponent notation."""
    text = f"{bound:,.2f}"
    return text[:-3] if text.endswith(".00") else text


def parse_optional_int(value: Any, field_name: str, errors: list[str]) -> int | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        errors.append(f"{field_name} must be a whole number.")
        return None


def parse_required_int(value: Any, field_name: str, errors: list[str]) -> int:
    raw = _clean(value)
    if not raw:
        errors.append(f"{field_name} is required.")
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        errors.append(f"{field_name} must be a whole number.")
        return 0


def parse_float(value: Any, field_name: str, errors: list[str], default: float = 0) -> float:
    raw = _clean(value)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        errors.append(f"{field_name} must be a number.")
        return default


def parse_decimal_amount(
    value: Any,
    field_name: str,
    errors: list[str],
    default: float = 0.0,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_negative: bool = False,
) -> float:
    """Parse a bounded, finite decimal from form input (ADR-0002 Ruling R3).

    ``parse_float`` is frozen and stays as it is. This is the additive replacement
    for value fields where a nonsensical number must not reach the database:
    Python's ``float()`` happily accepts ``nan``, ``inf``, ``-inf`` and underscore
    separators such as ``1_000``, all of which propagated into ``CostLine`` and
    then into qualifying amounts, report totals and CSV exports.

    Every rejection appends a human-readable message to ``errors`` and returns
    ``default``; nothing is silently coerced.

    ``maximum`` is a *tightening* argument only (ADR-0002 Ruling R6). Passing ``None``
    no longer means unbounded, and passing a ceiling above ``MAX_MONETARY_AMOUNT`` no
    longer raises it: see ``effective_maximum``.
    """
    raw = _clean(value)
    if not raw:
        return default
    if "_" in raw:
        errors.append(f"{field_name} must be a plain number without underscore separators.")
        return default
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{field_name} must be a number.")
        return default
    if not math.isfinite(parsed):
        errors.append(f"{field_name} must be a real number. Values such as nan, inf and -inf are not accepted.")
        return default
    if not allow_negative and parsed < 0:
        errors.append(f"{field_name} cannot be negative.")
        return default
    if minimum is not None and parsed < minimum:
        errors.append(f"{field_name} must be {_format_bound(minimum)} or more.")
        return default
    bound = effective_maximum(maximum)
    if parsed > bound:
        errors.append(f"{field_name} must be {_format_bound(bound)} or less.")
        return default
    return parsed


def check_calculated_amount(
    value: float,
    field_name: str,
    errors: list[str],
    hint: str,
    maximum: float = MAX_MONETARY_AMOUNT,
) -> bool:
    """Bound a figure the Hub derived, not one the user typed (ADR-0002 Ruling R6).

    Bounding each input is not the same as bounding the product of two of them, and it is
    the product that is written to the claim. This applies the same limit and the same
    plain phrasing to a resolved figure, appending to the caller's ``errors`` list so the
    rejection surfaces through the existing ``validation_error_response`` path.

    ``hint`` names the fields to look at, because the number in the message is not one the
    user typed anywhere on the form.
    """
    if not math.isfinite(value):
        errors.append(
            f"{field_name} must be a real number. Values such as nan, inf and -inf are not accepted. {hint}"
        )
        return False
    if value > maximum:
        errors.append(f"{field_name} must be {_format_bound(maximum)} or less. {hint}")
        return False
    return True


def parse_money(
    value: Any,
    field_name: str,
    errors: list[str],
    default: float = 0.0,
    allow_negative: bool = False,
    maximum: float | None = MAX_MONETARY_AMOUNT,
) -> float:
    """Parse a monetary value: finite, non-negative by default, and bounded."""
    return parse_decimal_amount(
        value,
        field_name,
        errors,
        default=default,
        minimum=None,
        maximum=maximum,
        allow_negative=allow_negative,
    )


def parse_percentage(
    value: Any,
    field_name: str,
    errors: list[str],
    default: float = 0.0,
    maximum: float = 100.0,
) -> float:
    """Parse a percentage: finite, and within 0 to ``maximum`` inclusive."""
    return parse_decimal_amount(
        value,
        field_name,
        errors,
        default=default,
        minimum=0.0,
        maximum=maximum,
        allow_negative=False,
    )


def parse_optional_date(value: Any, field_name: str, errors: list[str]) -> date | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        errors.append(f"{field_name} must be a valid date in YYYY-MM-DD format.")
        return None


def parse_required_date(value: Any, field_name: str, errors: list[str]) -> date:
    parsed = parse_optional_date(value, field_name, errors)
    if parsed is None and not _clean(value):
        errors.append(f"{field_name} is required.")
    return parsed or date.today()


def parse_bool(value: Any) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def parse_enum(
    value: Any,
    allowed_values: Iterable[str],
    field_name: str,
    errors: list[str],
    default: str,
) -> str:
    raw = _clean(value) or default
    allowed = set(allowed_values)
    if raw not in allowed:
        errors.append(f"{field_name} must be one of: {', '.join(sorted(allowed))}.")
        return default
    return raw


def validation_error_response(errors: list[str], back_href: str = "javascript:history.back()") -> HTMLResponse:
    """Render validation failures as HTML (ADR-0004 D7: escaping is mandatory).

    This is the one page in the Hub assembled by string interpolation rather than by
    Jinja, so it has no autoescaping to inherit. Every message and the back link are
    escaped here, at the boundary that builds the markup, so no caller can be the reason
    an injection lands: a caller that starts passing uploaded or otherwise attacker-shaped
    text must not have to know that this page is hand-built.
    """
    items = "".join(f"<li>{html.escape(str(error))}</li>" for error in errors)
    document = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Validation error - R&D Claim Evidence Hub</title>
    <link rel="stylesheet" href="/static/styles.css">
  </head>
  <body>
    <main class="container">
      <section class="panel warning">
        <h1>Check the submitted values</h1>
        <p>The form was not saved because one or more values could not be understood.</p>
        <ul>{items}</ul>
        <p><a href="{html.escape(str(back_href), quote=True)}">Go back and correct the form</a></p>
      </section>
    </main>
  </body>
</html>"""
    return HTMLResponse(document, status_code=400)

