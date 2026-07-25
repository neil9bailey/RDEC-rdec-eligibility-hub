"""ADR-0004 D7: ``validation_error_response`` must escape everything it interpolates.

``app/form_utils.py`` builds this page by string interpolation, so it is the one
user-facing page in the Hub with no Jinja autoescaping to inherit. Today's callers all
pass either a static message or a path segment FastAPI has already coerced to ``int``,
so this is a latent hazard rather than a live vulnerability -- which is exactly why it
needs a test: the next caller to pass uploaded text would turn it into a live one
silently, and D7 states the escaping requirement is non-negotiable.

These call the function directly. Going through a route would only ever exercise the
callers that cannot carry a payload, and would prove nothing about this boundary.
"""

from html.parser import HTMLParser

import pytest

from app.form_utils import validation_error_response


INJECTION_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "</ul><script>alert(1)</script><ul>",
    "\" onmouseover=\"alert(1)",
    "This file has a column called '<svg onload=alert(1)>' that companies do not use.",
]


class _Markup(HTMLParser):
    """Collect the tags and the visible text a browser would actually build."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attribute_values: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attribute_values.extend(value for _, value in attrs if value is not None)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def parsed(response) -> _Markup:
    markup = _Markup()
    markup.feed(response.body.decode("utf-8"))
    markup.close()
    return markup


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_an_injected_message_is_rendered_inert(payload):
    response = validation_error_response([payload])
    body = response.body.decode("utf-8")
    markup = parsed(response)

    assert response.status_code == 400
    # Inert: the payload became text, not elements. Asserting on the parsed document
    # rather than on the raw string is what makes this a claim about what a browser
    # builds instead of a claim about which characters happen to be present.
    assert "script" not in markup.tags
    assert "img" not in markup.tags
    assert "svg" not in markup.tags
    assert not any("alert(1)" in value for value in markup.attribute_values)
    assert payload not in body, "the payload survived into the markup unescaped"
    # Escaped, not stripped: the operator still reads the message they were sent.
    assert payload in "".join(markup.text)


def test_the_back_link_cannot_break_out_of_its_attribute():
    response = validation_error_response(
        ["Customer is required."],
        '/projects/1" onmouseover="alert(1)',
    )
    markup = parsed(response)

    assert 'onmouseover="alert(1)"' not in response.body.decode("utf-8")
    assert markup.attribute_values.count('/projects/1" onmouseover="alert(1)') == 1
    assert not any("alert" in tag for tag in markup.tags)


def test_the_default_back_link_is_unchanged_by_escaping():
    """The default is a literal with no escapable character, so it must pass through."""
    body = validation_error_response(["Customer is required."]).body.decode("utf-8")

    assert 'href="javascript:history.back()"' in body


def test_a_plain_message_still_reads_normally():
    """Escaping must not disfigure the ordinary message an operator sees."""
    body = validation_error_response(["Gross cost must be a number."]).body.decode("utf-8")

    assert "<li>Gross cost must be a number.</li>" in body
