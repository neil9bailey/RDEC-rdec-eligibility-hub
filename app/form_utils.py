from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from fastapi.responses import HTMLResponse


def _clean(value: Any) -> str:
    return str(value or "").strip()


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
    items = "".join(f"<li>{error}</li>" for error in errors)
    html = f"""<!doctype html>
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
        <p><a href="{back_href}">Go back and correct the form</a></p>
      </section>
    </main>
  </body>
</html>"""
    return HTMLResponse(html, status_code=400)

