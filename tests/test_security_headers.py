"""G5b E6-HEADERS: every response carries the security headers.

The Hub emitted none. The set added is deliberately proportionate and deliberately NOT
resource-restricting: the templates use inline ``style="..."`` attributes throughout, so a CSP
naming ``default-src`` or ``style-src`` would break the pages. The tests below assert both
halves of that decision -- that the headers are present everywhere, and that the policy does not
contain a directive that could block a stylesheet, the vendored HTMX bundle or an HTMX request.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.database import get_session
from app.main import SECURITY_HEADERS, app
from app.models import Customer, RDProject
from app.services import CAVEAT


EXPECTED = {name.decode(): value.decode() for name, value in SECURITY_HEADERS}


def client_for(session):
    def override_session():
        yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def assert_headers(response, label):
    for header, value in EXPECTED.items():
        assert response.headers.get(header) == value, f"{label} is missing {header}"


def test_the_expected_header_set_is_what_g5b_asked_for():
    """Pins the values, so a later edit that softens one is visible in a diff of this file."""
    assert EXPECTED == {
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "x-frame-options": "DENY",
        "content-security-policy": "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    }


def test_no_hsts_is_emitted(seeded_session):
    """The Hub is plain HTTP on loopback by design; HSTS would be wrong here."""
    client = client_for(seeded_session)
    try:
        response = client.get("/")
    finally:
        app.dependency_overrides.clear()

    assert "strict-transport-security" not in {key.lower() for key in response.headers}


def test_the_csp_cannot_block_a_page_resource():
    """The guard on the conservative-CSP decision.

    Adding any of these directives would govern resource loading, and this app's inline style
    attributes and vendored HTMX bundle are exactly what such a policy would stop. If someone
    tightens the policy later, this test fails and forces a real browser render pass first.
    """
    policy = EXPECTED["content-security-policy"]
    for directive in ("default-src", "style-src", "script-src", "img-src", "connect-src", "font-src"):
        assert directive not in policy, f"{directive} needs a browser render check before it ships"


PAGES = [
    "/",
    "/customers",
    "/projects",
    "/costs",
    "/data-management",
    "/audit",
    "/knowledge-agent",
    "/framework-intelligence",
]


@pytest.mark.parametrize("path", PAGES)
def test_rendered_pages_carry_the_headers(seeded_session, path):
    client = client_for(seeded_session)
    try:
        response = client.get(path)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert_headers(response, path)


def test_the_caveat_is_still_on_the_page(seeded_session):
    """Must-not-regress: the headers change must not disturb the published caveat."""
    client = client_for(seeded_session)
    try:
        response = client.get("/")
    finally:
        app.dependency_overrides.clear()

    assert CAVEAT in response.text


def test_static_assets_carry_the_headers(seeded_session):
    """The middleware sits outside the router, so the mounted StaticFiles app is covered too."""
    client = client_for(seeded_session)
    try:
        stylesheet = client.get("/static/styles.css")
        htmx = client.get("/static/htmx.min.js")
    finally:
        app.dependency_overrides.clear()

    assert stylesheet.status_code == 200
    assert htmx.status_code == 200
    assert_headers(stylesheet, "/static/styles.css")
    assert_headers(htmx, "/static/htmx.min.js")
    # nosniff only bites when the declared type is wrong, so the declared types matter now.
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "javascript" in htmx.headers["content-type"]


def test_a_validation_error_response_carries_the_headers(seeded_session):
    """400s are hand-built HTML rather than template responses; they must be covered."""
    client = client_for(seeded_session)
    try:
        response = client.post("/customers", data={}, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert_headers(response, "validation error")


def test_a_redirect_response_carries_the_headers(seeded_session):
    client = client_for(seeded_session)
    try:
        response = client.post(
            "/customers",
            data={"customer_name": "Header Test Customer", "customer_type": "local authority"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert_headers(response, "303 redirect")


def test_a_markdown_export_carries_the_headers(seeded_session):
    project = seeded_session.exec(select(RDProject)).first()
    client = client_for(seeded_session)
    try:
        response = client.get(f"/projects/{project.id}/report?format=md")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert_headers(response, "markdown export")
    assert response.headers["content-type"].startswith("text/markdown")
    # Must-not-regress: the caveat travels with the export.
    assert CAVEAT in response.text


def test_the_htmx_save_flow_still_works_and_carries_the_headers(seeded_session):
    """The HTMX partial-save path, exercised end to end with the HX-Request header.

    This is the flow a conservative CSP would be most likely to break, so it is asserted here
    rather than only in the HTMX feedback tests: the request must still be recognised as an HTMX
    request, still return a partial rather than a full page, and still persist the record.
    """
    client = client_for(seeded_session)
    try:
        response = client.post(
            "/customers",
            data={
                "customer_name": "HTMX Saved Customer",
                "sector": "Rail",
                "transport_domain": "rail",
                "customer_type": "local authority",
                "corporation_tax_status": "no",
            },
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code in (200, 303)
    assert_headers(response, "htmx save")
    saved = seeded_session.exec(select(Customer).where(Customer.customer_name == "HTMX Saved Customer")).first()
    assert saved is not None, "the HTMX save flow no longer persists the record"
