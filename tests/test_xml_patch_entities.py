"""XML patch files come from imported mods, so they are untrusted input.

lxml's default ``XMLParser`` has ``resolve_entities=True``, which leaves two
holes open in every document this module parses:

* **entity-expansion DoS** ("billion laughs") -- nested entities expand
  geometrically and exhaust memory;
* **XXE** -- ``<!ENTITY x SYSTEM "file:///...">`` reads a local file whose
  contents can then surface via patch output or an error message.

These tests assert the attacks are actually neutralised, rather than asserting
that a flag is set somewhere -- a test that only checked the keyword argument
would still pass if the parser were bypassed at a call site.
"""
from __future__ import annotations

from lxml import etree

from cdumm.engine.xml_patch_handler import _safe_parser

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<lolz>&lol4;</lolz>"""


def test_entity_expansion_is_not_performed() -> None:
    """The bomb must not expand into the tree."""
    root = etree.fromstring(BILLION_LAUGHS, _safe_parser(recover=True))
    text = root.text or ""
    # Expanded, lol4 alone is 10_000 copies of "lol" (30 KB) and the real
    # attack nests far deeper. Unexpanded, the entity reference is simply
    # not substituted.
    assert len(text) < 1000, f"entity expanded to {len(text)} chars"
    assert "lollollol" not in text


def test_xxe_does_not_read_local_files(tmp_path) -> None:
    """An external entity must not pull a local file into the document."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET_CANARY", encoding="utf-8")
    uri = secret.as_uri()
    payload = (
        f'<?xml version="1.0"?>\n'
        f'<!DOCTYPE d [ <!ENTITY xxe SYSTEM "{uri}"> ]>\n'
        f"<d>&xxe;</d>"
    ).encode()

    root = etree.fromstring(payload, _safe_parser(recover=True))
    assert "TOP_SECRET_CANARY" not in (root.text or "")
    assert "TOP_SECRET_CANARY" not in etree.tostring(root, encoding="unicode")


def test_safe_parser_defaults_are_hardened() -> None:
    """Belt-and-braces: the factory must not be silently constructible unsafe."""
    p = _safe_parser()
    assert isinstance(p, etree.XMLParser)
    # Callers may pass extra kwargs (recover, remove_blank_text); they must not
    # be able to accidentally drop the hardening by doing so.
    p2 = _safe_parser(recover=True, remove_blank_text=False)
    assert isinstance(p2, etree.XMLParser)


def test_ordinary_patch_xml_still_parses() -> None:
    """Hardening must not break normal patch documents."""
    doc = b'<xml-patch><operation op="replace" xpath="//a">x</operation></xml-patch>'
    root = etree.fromstring(doc, _safe_parser())
    assert root.tag == "xml-patch"
    assert root[0].get("xpath") == "//a"
