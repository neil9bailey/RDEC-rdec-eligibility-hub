"""E5-CAVEAT: the validation error page carries the ADR-0002 line 58 preserve-clause.

``app/form_utils.py validation_error_response`` is the one page in the Hub that is assembled
by string interpolation instead of by Jinja, so it inherits nothing from ``base.html`` -- not
the header, not the navigation, not the skip link, and not the caveat. Every other page and
all three Markdown exports carried "Requires competent professional and tax review."; this
one did not, which is exactly the shape of the export defect this epic exists to close: a
surface that is built separately gets humanised separately, or not at all.

ADR-0002 Ruling R2 states the clause protects the literal string *and the mechanism that
renders it*, and names ``app/services.py CAVEAT`` as its definition, so this module asserts
the page renders that constant rather than a matching copy of the sentence.

``tests/test_validation_error_escaping.py`` owns the ADR-0004 D7 escaping proof. The escaping
assertions repeated here are deliberate duplication: adding markup to this document is the
change most likely to weaken them, so the increment that adds the markup carries its own proof
that it did not.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from app.form_utils import validation_error_response
from app.services import CAVEAT


def visible_text(document: str) -> str:
    """What a browser would actually show, not what the source happens to contain."""

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.chunks: list[str] = []

        def handle_data(self, data: str) -> None:
            self.chunks.append(data)

    parser = _Text()
    parser.feed(document)
    parser.close()
    return " ".join(parser.chunks)


def test_the_caveat_is_definitionally_the_preserved_string():
    assert CAVEAT == "Requires competent professional and tax review."


def test_the_validation_error_page_shows_the_caveat():
    response = validation_error_response(["Gross cost must be a number."])
    document = response.body.decode("utf-8")

    assert CAVEAT in document
    # A reviewer must be able to read it, not just find it in the source.
    assert CAVEAT in visible_text(document)


def test_the_caveat_survives_an_empty_error_list_and_a_custom_back_link():
    """The clause is unconditional: it does not depend on what the caller passed."""
    for errors, back in ([], "/projects"), (["A."], "javascript:history.back()"), (["A.", "B."], "/"):
        document = validation_error_response(errors, back).body.decode("utf-8")
        assert CAVEAT in visible_text(document), f"caveat missing for errors={errors!r}"


def test_the_page_still_returns_400_for_a_non_htmx_caller():
    """ADR-0002 R2 protects the mechanism, not the status. The status is protected here.

    htmx callers are served by ``partial_validation_response`` in app/main.py, which returns
    200 for transport reasons. This function serves everyone else and must keep saying 400.
    """
    response = validation_error_response(["Gross cost must be a number."])

    assert response.status_code == 400
    assert response.media_type == "text/html"
    assert document_is_complete(response.body.decode("utf-8"))


def document_is_complete(document: str) -> bool:
    return document.startswith("<!doctype html>") and document.rstrip().endswith("</html>")


def test_adding_the_caveat_did_not_weaken_the_escaping():
    """ADR-0004 D7. The new markup must not have moved anything outside the escaped boundary."""
    payload = "<script>alert(1)</script>"
    response = validation_error_response([payload], '/projects/1" onmouseover="alert(1)')
    document = response.body.decode("utf-8")

    assert payload not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert 'onmouseover="alert(1)"' not in document
    assert payload in visible_text(document)


def test_the_caveat_is_not_a_second_copy_of_the_sentence():
    """A duplicated literal is a literal that can drift. The page must render the constant.

    Asserted on the source, because a copy of the sentence would satisfy every test above.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "form_utils.py").read_text(encoding="utf-8")

    assert "from app.services import CAVEAT" in source
    assert not re.search(r"Requires competent professional and tax review\.", source), (
        "app/form_utils.py must render the imported CAVEAT constant, not a copy of the string"
    )
