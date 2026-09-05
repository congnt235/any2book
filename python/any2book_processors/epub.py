from __future__ import annotations

import html
import ipaddress
import json
import posixpath
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as etree
import xml.parsers.expat as pyexpat
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import quote, unquote, urljoin, urlsplit

import idna
import tree_sitter_javascript
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import DefusedXMLParser
from tree_sitter import Language, Node, Parser, Point

from .models import BookDocument, ConversionWarning

_CSS = """body { font-family: serif; line-height: 1.55; margin: 5%; }
img, svg { display: block; max-width: 100%; height: auto; margin: 1.25em auto; }
figure { margin: 1.5em auto; text-align: center; break-inside: avoid; page-break-inside: avoid; }
figure img, figure svg { margin-left: auto; margin-right: auto; }
figcaption { margin-top: .5em; text-align: center; font-size: .9em; }
table { border-collapse: collapse; max-width: 100%; }
th, td { border: 1px solid #888; padding: .3em; }
pre, code { white-space: pre-wrap; overflow-wrap: anywhere; }
a { text-decoration: none; }
.ornament { letter-spacing: .75em; margin: 1.5em 0; text-align: center; }
"""


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing dependency: {command[0]}. Run `any2book doctor`.") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Command failed: {' '.join(command)}")
    return result


def render(document: BookDocument, output: Path, work_dir: Path, config: dict[str, Any]) -> None:
    source = work_dir / "normalized.html"
    css = work_dir / "book.css"
    css.write_text(_CSS, encoding="utf-8")
    title = html.escape(str(document.metadata["title"]))
    chapters = []
    for chapter in document.chapters:
        chapters.append(f"<section><h1>{html.escape(chapter.title)}</h1>{chapter.html}</section>")
    source.write_text(
        f'<!doctype html><html lang="{html.escape(str(document.metadata["language"]))}">'
        f'<head><meta charset="utf-8"><title>{title}</title></head>'
        f"<body>{''.join(chapters)}</body></html>",
        encoding="utf-8",
    )
    command = [
        "pandoc",
        str(source),
        "--from=html",
        "--to=epub3",
        "--output",
        str(output),
        "--css",
        str(css),
        "--metadata",
        f"title={document.metadata['title']}",
        "--metadata",
        f"lang={document.metadata['language']}",
        "--split-level",
        str(config["conversion"]["splitLevel"]),
        "--resource-path",
        str(work_dir),
    ]
    authors = cast(list[object], document.metadata.get("authors", []))
    for author in authors:
        command.extend(["--metadata", f"author={author}"])
    if config["conversion"]["tableOfContents"] != "none":
        command.append("--toc")
    cover = document.metadata.get("cover")
    if cover:
        command.extend(["--epub-cover-image", str(cover)])
    _run(command)


def _xml_root(archive: zipfile.ZipFile, name: str, label: str) -> etree.Element:
    with archive.open(name) as source:
        chunks = iter(lambda: source.read(64 * 1024), b"")
        return _bounded_xml_root(chunks, label)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _qualified(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _archive_reference(package_path: str, href: str) -> str | None:
    reference = urlsplit(href)
    if reference.scheme or reference.netloc:
        if reference.scheme not in {"http", "https"} or not reference.netloc:
            raise RuntimeError(f"Invalid EPUB: unsafe remote manifest resource: {href}")
        return None
    decoded = unquote(reference.path)
    if not decoded or decoded.startswith(("/", "\\")) or "\\" in decoded:
        raise RuntimeError(f"Invalid EPUB: unsafe package resource: {href}")
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), decoded))
    if normalized == ".." or normalized.startswith("../"):
        raise RuntimeError(f"Invalid EPUB: unsafe package resource: {href}")
    return normalized


ManifestItem = tuple[str | None, str, set[str], str | None]

_REMOTE_FONT_MEDIA_TYPES = {
    "application/font-sfnt",
    "application/font-woff",
    "application/vnd.ms-fontobject",
    "application/vnd.ms-opentype",
}
_JAVASCRIPT_MEDIA_TYPES = {
    "application/ecmascript",
    "application/javascript",
    "application/x-ecmascript",
    "application/x-javascript",
    "text/ecmascript",
    "text/javascript",
    "text/javascript1.0",
    "text/javascript1.1",
    "text/javascript1.2",
    "text/javascript1.3",
    "text/javascript1.4",
    "text/javascript1.5",
    "text/jscript",
    "text/livescript",
    "text/x-ecmascript",
    "text/x-javascript",
}
_SVG_URL_PRESENTATION_ATTRIBUTES = {
    "clip-path",
    "color-profile",
    "cursor",
    "fill",
    "filter",
    "marker",
    "marker-end",
    "marker-mid",
    "marker-start",
    "mask",
    "shape-inside",
    "shape-outside",
    "shape-subtract",
    "stroke",
}
_FETCHING_LINK_RELATIONS = {"icon", "modulepreload", "prefetch", "preload", "stylesheet"}
_INTERNAL_URL_HOST = "any2book-container.invalid"
_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_OCF_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"
_OPF_NAMESPACE = "http://www.idpf.org/2007/opf"
_XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_DTBOOK_NAMESPACE = "http://www.daisy.org/z3986/2005/dtbook/"
_NCX_NAMESPACE = "http://www.daisy.org/z3986/2005/ncx/"
_SMIL_NAMESPACE = "http://www.w3.org/ns/SMIL"
_MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_URL_ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")
_URL_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
_MAX_PARSED_RESOURCE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_PARSED_BYTES = 256 * 1024 * 1024
_MAX_XML_ELEMENTS = 200_000
_MAX_XML_DEPTH = 4_096
_MAX_CSS_NESTING = 4_096
_MAX_CSS_ESCAPES_PER_TOKEN = 200_000
_MAX_JAVASCRIPT_BYTES = 2 * 1024 * 1024
_MAX_JAVASCRIPT_NODES = 500_000
_MAX_JAVASCRIPT_PARSE_OPERATIONS = 2_500_000
_MAX_REFERENCES_PER_RESOURCE = 20_000
_MAX_SPINE_FALLBACK_STEPS = 500_000
_JAVASCRIPT_LANGUAGE = Language(tree_sitter_javascript.language())
_EXPAT_VERSION = pyexpat.version_info

_XML_REFERENCE_MEDIA_TYPES = {
    "application/mathml+xml",
    "application/smil+xml",
    "application/xhtml+xml",
    "application/x-dtbncx+xml",
    "application/x-dtbook+xml",
    "image/svg+xml",
    "text/x-oeb1-document",
}
_EMPTY_ATTRIBUTES: frozenset[str] = frozenset()
_XHTML_RESOURCE_ATTRIBUTES = {
    "audio": frozenset({"src"}),
    "embed": frozenset({"src"}),
    "iframe": frozenset({"src"}),
    "img": frozenset({"src"}),
    "input": frozenset({"src"}),
    "link": frozenset({"href"}),
    "object": frozenset({"data"}),
    "script": frozenset({"src"}),
    "source": frozenset({"src"}),
    "track": frozenset({"src"}),
    "video": frozenset({"poster", "src"}),
}
_DTBOOK_RESOURCE_ATTRIBUTES = {
    "img": frozenset({"src"}),
    "link": frozenset({"href"}),
    "object": frozenset({"data"}),
}

ReferenceKind = Literal["embedded", "script"]
NavigationReference = tuple[str, str]
ParsedResources = dict[str, int]
JavaScriptBudget = list[int]
ResourceReference = tuple[str, ReferenceKind, bool]
ResourceScan = tuple[set[ResourceReference], bool]
RemoteReferenceCache = dict[tuple[str, str], ResourceScan]

_JAVASCRIPT_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}


class _BoundedTreeBuilder:
    def __init__(self) -> None:
        self._builder = etree.TreeBuilder()
        self._elements = 0
        self._depth = 0
        self._expanded_characters = 0

    def _charge_text(self, length: int) -> None:
        self._expanded_characters += length
        if self._expanded_characters > _MAX_PARSED_RESOURCE_BYTES:
            raise RuntimeError("Invalid EPUB: expanded XML text budget exceeded")

    def start(self, tag: str, attributes: dict[str, str]) -> etree.Element:
        if self._elements >= _MAX_XML_ELEMENTS:
            raise RuntimeError("Invalid EPUB: XML element budget exceeded")
        if self._depth >= _MAX_XML_DEPTH:
            raise RuntimeError("Invalid EPUB: XML depth budget exceeded")
        self._elements += 1
        self._depth += 1
        self._charge_text(
            len(tag) + sum(len(name) + len(value) for name, value in attributes.items())
        )
        return self._builder.start(tag, attributes)

    def end(self, tag: str) -> etree.Element:
        element = self._builder.end(tag)
        self._depth -= 1
        return element

    def data(self, data: str) -> None:
        self._charge_text(len(data))
        self._builder.data(data)

    def close(self) -> etree.Element:
        return self._builder.close()


def _bounded_xml_root(chunks: Iterable[bytes], label: str) -> etree.Element:
    if _EXPAT_VERSION < (2, 6, 0):
        raise RuntimeError(
            "Unsafe XML parser: Any2Book EPUB validation requires Expat 2.6.0 or newer"
        )
    parser = DefusedXMLParser(
        target=_BoundedTreeBuilder(),
        forbid_dtd=False,
        forbid_entities=True,
        forbid_external=True,
    )
    try:
        for chunk in chunks:
            parser.feed(chunk)
        return parser.close()
    except DefusedXmlException as exc:
        raise RuntimeError(f"Invalid EPUB: unsafe DTD or entity in {label}") from exc
    except etree.ParseError as exc:
        raise RuntimeError(f"Invalid EPUB: malformed {label}") from exc


def _validate_fallback_chains(items: dict[str, ManifestItem]) -> None:
    validated: set[str] = set()
    for item_id in items:
        if item_id in validated:
            continue
        current = item_id
        chain: list[str] = []
        chain_items: set[str] = set()
        while current not in validated:
            if current in chain_items:
                raise RuntimeError(f"Invalid EPUB: cyclic manifest fallback from: {item_id}")
            chain.append(current)
            chain_items.add(current)
            fallback = items[current][3]
            if fallback is None:
                break
            if fallback not in items:
                raise RuntimeError(f"Invalid EPUB: unknown manifest fallback: {fallback}")
            current = fallback
        validated.update(chain)


def _claim_parsed_resource(
    archive: zipfile.ZipFile, resource_path: str, parsed_resources: ParsedResources
) -> None:
    if resource_path in parsed_resources:
        return
    size = archive.getinfo(resource_path).file_size
    if size > _MAX_PARSED_RESOURCE_BYTES:
        raise RuntimeError(f"Invalid EPUB: parsed resource exceeds 64 MiB: {resource_path}")
    if sum(parsed_resources.values()) + size > _MAX_TOTAL_PARSED_BYTES:
        raise RuntimeError("Invalid EPUB: parsed resources exceed the 256 MiB budget")
    parsed_resources[resource_path] = size


def _url_component(value: str, safe: str) -> str:
    encoded = quote(value, safe=f"{safe}%")
    encoded = re.sub(r"%(?![0-9a-fA-F]{2})", "%25", encoded)

    def canonical_escape(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        return character if character in _URL_UNRESERVED else f"%{match.group(1).upper()}"

    return _URL_ESCAPE.sub(canonical_escape, encoded)


def _remove_url_dot_segments(path: str) -> str:
    segments: list[str] = []
    for segment in path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if segments and segments[-1]:
                segments.pop()
            continue
        segments.append(segment)
    normalized = "/".join(segments)
    if path.endswith(("/", "/.", "/..")) and not normalized.endswith("/"):
        normalized += "/"
    return normalized or "/"


def _remote_key(href: str) -> str:
    reference = urlsplit(href)
    scheme = reference.scheme.lower()
    raw_hostname = reference.hostname or ""
    if ":" in raw_hostname:
        try:
            hostname = f"[{ipaddress.IPv6Address(raw_hostname).compressed.lower()}]"
        except ipaddress.AddressValueError as exc:
            raise RuntimeError(f"Invalid EPUB: invalid remote resource URL: {href}") from exc
    else:
        try:
            decoded_hostname = unquote(raw_hostname, errors="strict").rstrip(".")
            hostname = (
                idna.encode(decoded_hostname, uts46=True, std3_rules=True).decode("ascii").lower()
            )
        except (UnicodeError, idna.IDNAError) as exc:
            raise RuntimeError(f"Invalid EPUB: invalid remote resource URL: {href}") from exc
    try:
        port = reference.port
    except ValueError as exc:
        raise RuntimeError(f"Invalid EPUB: invalid remote resource URL: {href}") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    authority = hostname if port is None or default_port else f"{hostname}:{port}"
    if reference.username is not None:
        userinfo_safe = "!$&'()*+,;=:"
        userinfo = _url_component(reference.username, userinfo_safe)
        if reference.password is not None:
            password = _url_component(reference.password, userinfo_safe)
            userinfo += f":{password}"
        authority = f"{userinfo}@{authority}"
    path = _url_component(reference.path or "/", "/:@!$&'()*+,;=")
    path = _remove_url_dot_segments(path)
    query = _url_component(reference.query, "/?:@!$&'()*+,;=")
    return reference._replace(
        scheme=scheme,
        netloc=authority,
        path=path,
        query=query,
        fragment="",
    ).geturl()


def _css_escape(value: str, index: int) -> tuple[str, int]:
    index += 1
    if index >= len(value):
        return "", index
    if value[index] in "\n\f":
        return "", index + 1
    if value[index] == "\r":
        return "", index + 2 if value[index : index + 2] == "\r\n" else index + 1
    if value[index].lower() in "0123456789abcdef":
        end = index
        while end < len(value) and end - index < 6 and value[end].lower() in "0123456789abcdef":
            end += 1
        codepoint = int(value[index:end], 16)
        if end < len(value) and value[end].isspace():
            end += 1
        if codepoint == 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return "\N{REPLACEMENT CHARACTER}", end
        return chr(codepoint), end
    return value[index], index + 1


def _css_string(value: str, index: int) -> tuple[str | None, int]:
    quote_character = value[index]
    index += 1
    segment_start = index
    fragments: list[str] = []
    escape_count = 0
    while index < len(value):
        character = value[index]
        if character == quote_character:
            if not fragments:
                return value[segment_start:index], index + 1
            fragments.append(value[segment_start:index])
            return "".join(fragments), index + 1
        if character in "\n\r\f":
            return None, index + 1
        if character == "\\":
            escape_count += 1
            if escape_count > _MAX_CSS_ESCAPES_PER_TOKEN:
                raise RuntimeError("Invalid EPUB: CSS token budget exceeded")
            fragments.append(value[segment_start:index])
            escaped, index = _css_escape(value, index)
            fragments.append(escaped)
            segment_start = index
            continue
        index += 1
    return None, index


def _skip_css_space_and_comments(value: str, index: int) -> int:
    while index < len(value):
        if value[index].isspace():
            index += 1
            continue
        if value.startswith("/*", index):
            end = value.find("*/", index + 2)
            if end < 0:
                return len(value)
            index = end + 2
            continue
        break
    return index


def _css_identifier(value: str, index: int) -> tuple[str, int]:
    segment_start = index
    fragments: list[str] = []
    escape_count = 0
    while index < len(value):
        character = value[index]
        if character.isalnum() or character in {"_", "-"} or ord(character) >= 0x80:
            index += 1
            continue
        if character == "\\" and index + 1 < len(value) and value[index + 1] not in "\n\r\f":
            escape_count += 1
            if escape_count > _MAX_CSS_ESCAPES_PER_TOKEN:
                raise RuntimeError("Invalid EPUB: CSS token budget exceeded")
            fragments.append(value[segment_start:index])
            escaped, index = _css_escape(value, index)
            fragments.append(escaped)
            segment_start = index
            continue
        break
    if not fragments:
        return value[segment_start:index], index
    fragments.append(value[segment_start:index])
    return "".join(fragments), index


def _css_url(value: str, index: int) -> tuple[str | None, int]:
    index = _skip_css_space_and_comments(value, index)
    if index >= len(value) or value[index] != "(":
        return None, index
    index = _skip_css_space_and_comments(value, index + 1)
    if index < len(value) and value[index] in {'"', "'"}:
        reference, index = _css_string(value, index)
        index = _skip_css_space_and_comments(value, index)
        if reference is not None and index < len(value) and value[index] == ")":
            return reference, index + 1
        return None, index
    segment_start = index
    fragments: list[str] = []
    escape_count = 0

    def content(end: int) -> str:
        if not fragments:
            return value[segment_start:end]
        fragments.append(value[segment_start:end])
        return "".join(fragments)

    while index < len(value):
        character = value[index]
        if character == ")":
            return content(index), index + 1
        if character.isspace():
            content_end = index
            index = _skip_css_space_and_comments(value, index)
            if index < len(value) and value[index] == ")":
                return content(content_end), index + 1
            return None, index
        if character in {'"', "'", "("}:
            return None, index + 1
        if character == "\\":
            escape_count += 1
            if escape_count > _MAX_CSS_ESCAPES_PER_TOKEN:
                raise RuntimeError("Invalid EPUB: CSS token budget exceeded")
            fragments.append(value[segment_start:index])
            escaped, index = _css_escape(value, index)
            fragments.append(escaped)
            segment_start = index
            continue
        index += 1
    return None, index


def _css_at_rule_end(value: str, index: int) -> int:
    depth = 0
    while index < len(value):
        if value.startswith("/*", index):
            index = _skip_css_space_and_comments(value, index)
            continue
        character = value[index]
        if character in {'"', "'"}:
            _, index = _css_string(value, index)
            continue
        if character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif character == ";" and depth == 0:
            return index + 1
        index += 1
    return index


def _css_references(value: str) -> set[str]:
    references: set[str] = set()
    function_stack: list[bool | None] = []

    def add(reference: str) -> None:
        if reference not in references and len(references) >= _MAX_REFERENCES_PER_RESOURCE:
            raise RuntimeError("Invalid EPUB: resource reference budget exceeded")
        references.add(reference)

    def consume_image_candidate() -> None:
        if function_stack and function_stack[-1] is not None:
            function_stack[-1] = False

    def push_function(candidate: bool | None) -> None:
        if len(function_stack) >= _MAX_CSS_NESTING:
            raise RuntimeError("Invalid EPUB: CSS nesting budget exceeded")
        function_stack.append(candidate)

    index = 0
    while index < len(value):
        if value.startswith("/*", index):
            index = _skip_css_space_and_comments(value, index)
            continue
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character in {'"', "'"}:
            reference, index = _css_string(value, index)
            if function_stack and function_stack[-1] is True and reference:
                add(reference)
            consume_image_candidate()
            continue
        if character == "@":
            consume_image_candidate()
            keyword, next_index = _css_identifier(value, index + 1)
            if keyword.lower() == "import":
                import_index = _skip_css_space_and_comments(value, next_index)
                if import_index < len(value) and value[import_index] in {'"', "'"}:
                    reference, next_index = _css_string(value, import_index)
                    if reference:
                        add(reference)
                    index = next_index
                    continue
            elif keyword.lower() == "namespace":
                index = _css_at_rule_end(value, next_index)
                continue
            index = max(index + 1, next_index)
            continue
        if character == "(":
            consume_image_candidate()
            push_function(None)
            index += 1
            continue
        if character == ")":
            if function_stack:
                function_stack.pop()
            index += 1
            continue
        if character == ",":
            if function_stack and function_stack[-1] is not None:
                function_stack[-1] = True
            index += 1
            continue
        identifier, next_index = _css_identifier(value, index)
        if identifier:
            if identifier.lower() == "url":
                consume_image_candidate()
                reference, url_end = _css_url(value, next_index)
                if reference:
                    add(reference)
                index = max(next_index, url_end)
            elif identifier.lower() in {"image-set", "-webkit-image-set"}:
                consume_image_candidate()
                opening = _skip_css_space_and_comments(value, next_index)
                if opening < len(value) and value[opening] == "(":
                    push_function(True)
                    index = opening + 1
                else:
                    index = next_index
            else:
                consume_image_candidate()
                index = next_index
            continue
        consume_image_candidate()
        index += 1
    return references


def _srcset_references(value: str) -> set[str]:
    references: set[str] = set()
    index = 0
    while index < len(value):
        while index < len(value) and (value[index].isspace() or value[index] == ","):
            index += 1
        start = index
        while index < len(value) and not value[index].isspace():
            index += 1
        reference = value[start:index].rstrip(",")
        if reference:
            if reference not in references and len(references) >= _MAX_REFERENCES_PER_RESOURCE:
                raise RuntimeError("Invalid EPUB: resource reference budget exceeded")
            references.add(reference)
        if index > start and value[index - 1] == ",":
            continue
        while index < len(value) and value[index] != ",":
            index += 1
    return references


def _javascript_escape(value: str, index: int) -> tuple[str, int]:
    index += 1
    if index >= len(value):
        return "", index
    escaped = value[index]
    if escaped in {"\n", "\u2028", "\u2029"}:
        return "", index + 1
    if escaped == "\r":
        return "", index + 2 if value[index : index + 2] == "\r\n" else index + 1
    if escaped in _JAVASCRIPT_ESCAPES:
        return _JAVASCRIPT_ESCAPES[escaped], index + 1
    if escaped == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", value[index + 1 : index + 3]):
        return chr(int(value[index + 1 : index + 3], 16)), index + 3
    if escaped == "u":
        if index + 1 < len(value) and value[index + 1] == "{":
            end = value.find("}", index + 2)
            digits = value[index + 2 : end] if end >= 0 else ""
            if 1 <= len(digits) <= 6 and re.fullmatch(r"[0-9a-fA-F]+", digits):
                codepoint = int(digits, 16)
                if codepoint <= 0x10FFFF:
                    character = (
                        "\N{REPLACEMENT CHARACTER}"
                        if 0xD800 <= codepoint <= 0xDFFF
                        else chr(codepoint)
                    )
                    return character, end + 1
        digits = value[index + 1 : index + 5]
        if re.fullmatch(r"[0-9a-fA-F]{4}", digits):
            codepoint = int(digits, 16)
            if 0xD800 <= codepoint <= 0xDBFF and value[index + 5 : index + 7] == "\\u":
                low_digits = value[index + 7 : index + 11]
                if re.fullmatch(r"[0-9a-fA-F]{4}", low_digits):
                    low_surrogate = int(low_digits, 16)
                    if 0xDC00 <= low_surrogate <= 0xDFFF:
                        scalar = 0x10000 + ((codepoint - 0xD800) << 10) + low_surrogate - 0xDC00
                        return chr(scalar), index + 11
            character = (
                "\N{REPLACEMENT CHARACTER}" if 0xD800 <= codepoint <= 0xDFFF else chr(codepoint)
            )
            return character, index + 5
    if escaped in "01234567":
        end = index + 1
        while (
            end < len(value)
            and end - index < 3
            and value[end] in "01234567"
            and int(value[index : end + 1], 8) <= 0xFF
        ):
            end += 1
        return chr(int(value[index:end], 8)), end
    return escaped, index + 1


def _javascript_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _javascript_identifier_value(source: bytes, node: Node) -> str:
    raw = _javascript_text(source, node)
    decoded: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\":
            character, index = _javascript_escape(raw, index)
            decoded.append(character)
        else:
            decoded.append(raw[index])
            index += 1
    return "".join(decoded)


def _javascript_string_value(source: bytes, node: Node | None) -> str | None:
    if node is None or node.type not in {"string", "template_string"}:
        return None
    if node.type == "template_string" and any(
        child.type == "template_substitution" for child in node.named_children
    ):
        return None
    raw = _javascript_text(source, node)
    if len(raw) < 2:
        return None
    content: list[str] = []
    index = 1
    while index < len(raw) - 1:
        if raw[index] == "\\":
            character, index = _javascript_escape(raw, index)
            content.append(character)
        else:
            content.append(raw[index])
            index += 1
    return "".join(content)


def _javascript_template_fragment_value(source: bytes, node: Node) -> str:
    raw = _javascript_text(source, node)
    decoded: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\":
            character, index = _javascript_escape(raw, index)
            decoded.append(character)
        else:
            decoded.append(raw[index])
            index += 1
    return "".join(decoded)


def _javascript_static_path(
    source: bytes, expression: Node | None, constructor_depth: int = 0
) -> list[str] | None:
    if expression is None or constructor_depth > 32:
        return None
    suffix: list[str] = []
    while expression is not None:
        while expression.type == "parenthesized_expression":
            children = expression.named_children
            if len(children) != 1:
                return None
            expression = children[0]
        if expression.type == "identifier":
            return [
                _javascript_identifier_value(source, expression),
                *reversed(suffix),
            ]
        if expression.type == "member_expression":
            property_node = expression.child_by_field_name("property")
            if property_node is None:
                return None
            suffix.append(_javascript_identifier_value(source, property_node))
            expression = expression.child_by_field_name("object")
            continue
        if expression.type == "subscript_expression":
            property_name = _javascript_string_value(
                source, expression.child_by_field_name("index")
            )
            if property_name is None:
                return None
            suffix.append(property_name)
            expression = expression.child_by_field_name("object")
            continue
        if expression.type == "new_expression":
            constructor = _javascript_static_path(
                source,
                expression.child_by_field_name("constructor"),
                constructor_depth + 1,
            )
            if constructor is None:
                return None
            return [f"new:{'.'.join(constructor)}", *reversed(suffix)]
        return None
    return None


def _javascript_member(source: bytes, function: Node | None) -> tuple[str | None, str | None]:
    if function is None:
        return None, None
    while function.type == "parenthesized_expression":
        children = function.named_children
        if len(children) != 1:
            return None, None
        function = children[0]
    if function.type == "import":
        return None, _javascript_identifier_value(source, function)
    path = _javascript_static_path(source, function)
    if not path:
        return None, None
    return (".".join(path[:-1]) or None), path[-1]


def _javascript_binding_names(source: bytes, node: Node | None) -> set[str]:
    if node is None:
        return set()
    names: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if current.type in {"identifier", "shorthand_property_identifier_pattern"}:
            names.add(_javascript_identifier_value(source, current))
        elif current.type == "assignment_pattern":
            left = current.child_by_field_name("left")
            if left is not None:
                pending.append(left)
        elif current.type == "pair_pattern":
            value = current.child_by_field_name("value")
            if value is not None:
                pending.append(value)
        else:
            pending.extend(current.named_children)
    return names


def _javascript_import_binding_names(source: bytes, node: Node) -> set[str]:
    names: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if current.type == "import_specifier":
            local = current.child_by_field_name("alias") or current.child_by_field_name("name")
            names.update(_javascript_binding_names(source, local))
        elif (
            current.type == "identifier"
            and current.parent is not None
            and (current.parent.type in {"import_clause", "namespace_import"})
        ):
            names.add(_javascript_identifier_value(source, current))
        else:
            pending.extend(current.named_children)
    return names


def _script_remote_references(value: str, javascript_budget: JavaScriptBudget) -> set[str]:
    source = value.encode("utf-8")
    if len(source) > _MAX_JAVASCRIPT_BYTES:
        raise RuntimeError("Invalid EPUB: JavaScript resource exceeds 2 MiB")
    parser = Parser(_JAVASCRIPT_LANGUAGE)
    parse_aborted = False

    def limit_parse_operations(_kind: object, _message: str) -> None:
        nonlocal parse_aborted
        javascript_budget[1] += 1
        if javascript_budget[1] > _MAX_JAVASCRIPT_PARSE_OPERATIONS:
            parse_aborted = True

    def read_chunk(byte_offset: int, _point: Point) -> bytes:
        if parse_aborted:
            return b""
        return source[byte_offset : byte_offset + 64 * 1024]

    parser.logger = limit_parse_operations
    tree = parser.parse(read_chunk)
    if parse_aborted:
        raise RuntimeError("Invalid EPUB: JavaScript token budget exceeded")
    if tree is None:
        raise RuntimeError("Invalid EPUB: JavaScript parsing failed")
    references: set[str] = set()

    def add_reference(reference: str) -> None:
        if reference not in references and len(references) >= _MAX_REFERENCES_PER_RESOURCE:
            raise RuntimeError("Invalid EPUB: resource reference budget exceeded")
        references.add(reference)

    tracked_globals = {
        "SharedWorker",
        "Worker",
        "XMLHttpRequest",
        "document",
        "fetch",
        "globalThis",
        "importScripts",
        "open",
        "parent",
        "self",
        "top",
        "window",
    }
    function_scope_types = {
        "arrow_function",
        "function_declaration",
        "function_expression",
        "generator_function_declaration",
        "generator_function_expression",
        "method_definition",
        "program",
    }
    lexical_scope_types = function_scope_types | {
        "catch_clause",
        "for_in_statement",
        "for_statement",
        "statement_block",
        "switch_body",
    }
    BindingKey = tuple[tuple[str, int, int], str]
    scope_bindings: dict[tuple[str, int, int], set[str]] = {}
    all_scope_bindings: dict[tuple[str, int, int], set[str]] = {}
    alias_candidates: list[tuple[tuple[str, int, int], str, Node, str | None]] = []
    binding_values: dict[BindingKey, list[tuple[Node, str | None]]] = {}
    immutable_bindings: set[BindingKey] = set()
    constant_string_bindings: dict[BindingKey, Node] = {}

    def scope_key(node: Node) -> tuple[str, int, int]:
        return node.type, node.start_byte, node.end_byte

    def charge_scope_step() -> None:
        javascript_budget[0] += 1
        if javascript_budget[0] > _MAX_JAVASCRIPT_NODES:
            raise RuntimeError("Invalid EPUB: JavaScript token budget exceeded")

    def enclosing_scope(node: Node | None, function_only: bool = False) -> Node:
        allowed = function_scope_types if function_only else lexical_scope_types
        current = node
        while current is not None:
            charge_scope_step()
            if current.type in allowed:
                return current
            current = current.parent
        return tree.root_node

    def bind(scope: Node, binding: Node | None) -> set[str]:
        names = _javascript_binding_names(source, binding)
        return bind_names(scope, names)

    def bind_names(scope: Node, names: set[str]) -> set[str]:
        if names:
            key = scope_key(scope)
            all_scope_bindings.setdefault(key, set()).update(names)
            tracked_names = names & tracked_globals
            if tracked_names:
                scope_bindings.setdefault(key, set()).update(tracked_names)
        return names

    def destructured_open_aliases(pattern: Node | None) -> list[tuple[str, str]]:
        if pattern is None or pattern.type != "object_pattern":
            return []
        aliases: list[tuple[str, str]] = []
        for child in pattern.named_children:
            if child.type == "shorthand_property_identifier_pattern":
                name = _javascript_identifier_value(source, child)
                aliases.append((name, name))
            elif child.type == "pair_pattern":
                key = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")
                if key is None or value_node is None or value_node.type != "identifier":
                    continue
                aliases.append(
                    (
                        _javascript_identifier_value(source, value_node),
                        _javascript_identifier_value(source, key),
                    )
                )
        return aliases

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        javascript_budget[0] += 1
        if javascript_budget[0] > _MAX_JAVASCRIPT_NODES:
            raise RuntimeError("Invalid EPUB: JavaScript token budget exceeded")
        stack.extend(node.named_children)
        if node.type == "variable_declarator":
            declaration = node.parent
            # tree-sitter-javascript uses `variable_declaration` only for
            # `var`; `let` and `const` use `lexical_declaration` and therefore
            # bind to the nearest block below.
            function_scoped_var = (
                declaration is not None and declaration.type == "variable_declaration"
            )
            scope = enclosing_scope(
                declaration.parent if declaration is not None else node.parent,
                function_only=function_scoped_var,
            )
            name_node = node.child_by_field_name("name")
            bound_names = bind(scope, name_node)
            binding_scope_key = scope_key(scope)
            immutable_declaration = (
                declaration is not None
                and declaration.type == "lexical_declaration"
                and declaration.children
                and declaration.children[0].type == "const"
            )
            if immutable_declaration:
                immutable_bindings.update((binding_scope_key, name) for name in bound_names)
            value_node = node.child_by_field_name("value")
            if value_node is not None:
                if name_node is not None and name_node.type == "identifier":
                    binding_name = _javascript_identifier_value(source, name_node)
                    binding = (binding_scope_key, binding_name)
                    alias_candidates.append(
                        (
                            binding_scope_key,
                            binding_name,
                            value_node,
                            None,
                        )
                    )
                    binding_values.setdefault(binding, []).append((value_node, None))
                    if immutable_declaration:
                        constant_string_bindings[binding] = value_node
                else:
                    for alias, destructured_name in destructured_open_aliases(name_node):
                        binding = (binding_scope_key, alias)
                        alias_candidates.append(
                            (binding_scope_key, alias, value_node, destructured_name)
                        )
                        binding_values.setdefault(binding, []).append(
                            (value_node, destructured_name)
                        )
        elif node.type in {"function_declaration", "generator_function_declaration"}:
            bind(
                enclosing_scope(node.parent),
                node.child_by_field_name("name"),
            )
        elif node.type in {"function_expression", "generator_function_expression"}:
            bind(node, node.child_by_field_name("name"))
        elif node.type == "class_declaration":
            bind(enclosing_scope(node.parent), node.child_by_field_name("name"))
        elif node.type == "formal_parameters":
            bind(enclosing_scope(node.parent, function_only=True), node)
        elif node.type == "arrow_function":
            bind(node, node.child_by_field_name("parameter"))
        elif node.type == "catch_clause":
            bind(node, node.child_by_field_name("parameter"))
        elif node.type == "import_clause":
            bind_names(tree.root_node, _javascript_import_binding_names(source, node))

    def global_receiver_available(receiver: str | None, member: str, call_node: Node) -> bool:
        root = member if receiver is None else receiver.removeprefix("new:").split(".", 1)[0]
        current: Node | None = call_node
        while current is not None:
            charge_scope_step()
            bindings = scope_bindings.get(scope_key(current))
            if bindings is not None and root in bindings:
                return False
            current = current.parent
        return True

    browsing_contexts = {
        "document",
        "globalThis",
        "globalThis.document",
        "globalThis.window",
        "globalThis.window.document",
        "parent",
        "parent.document",
        "parent.window",
        "parent.window.document",
        "self",
        "self.document",
        "self.window",
        "self.window.document",
        "top",
        "top.document",
        "top.window",
        "top.window.document",
        "window",
        "window.document",
        "window.window",
        "window.window.document",
    }
    fetch_contexts = {None, "globalThis", "parent", "self", "top", "window"}
    worker_contexts = {None, "globalThis", "self"}

    def binding_scope(name: str, node: Node) -> tuple[str, int, int] | None:
        current: Node | None = node
        while current is not None:
            charge_scope_step()
            key = scope_key(current)
            bindings = all_scope_bindings.get(key)
            if bindings is not None and name in bindings:
                return key
            current = current.parent
        return None

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type != "assignment_expression":
            continue
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or left.type != "identifier" or right is None:
            continue
        name = _javascript_identifier_value(source, left)
        assigned_scope_key = binding_scope(name, node)
        if assigned_scope_key is not None:
            binding = (assigned_scope_key, name)
            if binding not in immutable_bindings:
                alias_candidates.append((assigned_scope_key, name, right, None))
                binding_values.setdefault(binding, []).append((right, None))

    for values in binding_values.values():
        values.sort(key=lambda candidate: candidate[0].start_byte)

    def binding_value_before(binding: BindingKey, use_node: Node) -> tuple[Node, str | None] | None:
        for candidate in reversed(binding_values.get(binding, [])):
            charge_scope_step()
            if candidate[0].start_byte < use_node.start_byte:
                return candidate
        return None

    direct_alias = object()

    def browsing_context_source(
        expression: Node | None, use_node: Node
    ) -> object | BindingKey | None:
        if expression is None:
            return None
        while expression.type == "parenthesized_expression":
            children = expression.named_children
            if len(children) != 1:
                return None
            expression = children[0]
        path = _javascript_static_path(source, expression)
        if path:
            context_path = ".".join(path)
            root = path[0]
            if context_path in browsing_contexts and global_receiver_available(
                None, root, use_node
            ):
                return direct_alias
            dependency_scope = binding_scope(root, use_node)
            if dependency_scope is not None and (
                len(path) == 1 or all(part in {"document", "window"} for part in path[1:])
            ):
                return (dependency_scope, root)
        if expression.type == "sequence_expression" and expression.named_children:
            return browsing_context_source(expression.named_children[-1], use_node)
        return None

    context_alias_dependants: dict[BindingKey, set[BindingKey]] = {}
    browsing_context_aliases: set[BindingKey] = set()
    for alias_scope_key, name, value_node, destructured_member in alias_candidates:
        alias_binding = (alias_scope_key, name)
        if destructured_member is not None or alias_binding not in immutable_bindings:
            continue
        context_source = browsing_context_source(value_node, value_node)
        if context_source is direct_alias:
            browsing_context_aliases.add(alias_binding)
        elif isinstance(context_source, tuple) and context_source in immutable_bindings:
            context_alias_dependants.setdefault(context_source, set()).add(alias_binding)

    pending_bindings = list(browsing_context_aliases)
    while pending_bindings:
        alias_binding = pending_bindings.pop()
        dependants = context_alias_dependants.get(alias_binding)
        if dependants is None:
            continue
        for dependant in dependants:
            if dependant not in browsing_context_aliases:
                browsing_context_aliases.add(dependant)
                pending_bindings.append(dependant)

    def is_browsing_context_expression(expression: Node | None, use_node: Node) -> bool:
        active_bindings: set[BindingKey] = set()
        while expression is not None:
            alias_source = browsing_context_source(expression, use_node)
            if alias_source is direct_alias:
                return True
            if not isinstance(alias_source, tuple):
                return False
            if alias_source in browsing_context_aliases:
                return True
            if alias_source in active_bindings:
                return False
            active_bindings.add(alias_source)
            previous_value = binding_value_before(alias_source, use_node)
            if previous_value is None or previous_value[1] is not None:
                return False
            expression = previous_value[0]
            use_node = expression
        return False

    def browsing_alias_source(
        expression: Node | None, use_node: Node
    ) -> object | BindingKey | None:
        if expression is None:
            return None
        while expression.type == "parenthesized_expression":
            children = expression.named_children
            if len(children) != 1:
                return None
            expression = children[0]
        path = _javascript_static_path(source, expression)
        if path:
            alias_receiver = ".".join(path[:-1]) or None
            member = path[-1]
            if member == "open" and (alias_receiver in browsing_contexts or alias_receiver is None):
                if global_receiver_available(alias_receiver, member, use_node):
                    return direct_alias
                if alias_receiver is None:
                    dependency_scope = binding_scope(member, use_node)
                    return (dependency_scope, member) if dependency_scope is not None else None
                return None
            if member == "open" and expression.type in {
                "member_expression",
                "subscript_expression",
            }:
                target = expression.child_by_field_name("object")
                if is_browsing_context_expression(target, use_node):
                    return direct_alias
            if len(path) == 1:
                dependency_scope = binding_scope(member, use_node)
                return (dependency_scope, member) if dependency_scope is not None else None
        if expression.type == "sequence_expression" and expression.named_children:
            return browsing_alias_source(expression.named_children[-1], use_node)
        if expression.type == "call_expression":
            function = expression.child_by_field_name("function")
            bind_receiver, bind_member = _javascript_member(source, function)
            if bind_receiver is not None and bind_member == "bind" and function is not None:
                target = function.child_by_field_name("object")
                return browsing_alias_source(target, use_node)
        return None

    alias_dependants: dict[BindingKey, set[BindingKey]] = {}
    browsing_alias_bindings: set[BindingKey] = set()
    for alias_scope_key, name, value_node, destructured_member in alias_candidates:
        alias_binding = (alias_scope_key, name)
        if alias_binding not in immutable_bindings:
            continue
        alias_source: object | BindingKey | None = None
        if destructured_member is None:
            alias_source = browsing_alias_source(value_node, value_node)
        elif destructured_member == "open":
            if is_browsing_context_expression(value_node, value_node):
                alias_source = direct_alias
        if alias_source is direct_alias:
            browsing_alias_bindings.add(alias_binding)
        elif isinstance(alias_source, tuple) and alias_source in immutable_bindings:
            alias_dependants.setdefault(alias_source, set()).add(alias_binding)

    pending_bindings = list(browsing_alias_bindings)
    while pending_bindings:
        alias_binding = pending_bindings.pop()
        dependants = alias_dependants.get(alias_binding)
        if dependants is None:
            continue
        for dependant in dependants:
            if dependant not in browsing_alias_bindings:
                browsing_alias_bindings.add(dependant)
                pending_bindings.append(dependant)

    def is_browsing_open_expression(expression: Node | None, use_node: Node) -> bool:
        active_bindings: set[BindingKey] = set()
        while expression is not None:
            alias_source = browsing_alias_source(expression, use_node)
            if alias_source is direct_alias:
                return True
            if not isinstance(alias_source, tuple):
                return False
            if alias_source in browsing_alias_bindings:
                return True
            if alias_source in active_bindings:
                return False
            active_bindings.add(alias_source)
            previous_value = binding_value_before(alias_source, use_node)
            if previous_value is None:
                return False
            expression, destructured_member = previous_value
            if destructured_member is not None:
                return destructured_member == "open" and is_browsing_context_expression(
                    expression, expression
                )
            use_node = expression
        return False

    def xhr_alias_source(expression: Node | None, use_node: Node) -> object | BindingKey | None:
        if expression is None:
            return None
        while expression.type == "parenthesized_expression":
            children = expression.named_children
            if len(children) != 1:
                return None
            expression = children[0]
        if expression.type == "new_expression":
            constructor_receiver, constructor_name = _javascript_member(
                source, expression.child_by_field_name("constructor")
            )
            if (
                constructor_name == "XMLHttpRequest"
                and constructor_receiver in fetch_contexts
                and global_receiver_available(constructor_receiver, constructor_name, use_node)
            ):
                return direct_alias
            return None
        path = _javascript_static_path(source, expression)
        if path and len(path) == 1:
            name = path[0]
            dependency_scope = binding_scope(name, use_node)
            return (dependency_scope, name) if dependency_scope is not None else None
        return None

    xhr_alias_dependants: dict[BindingKey, set[BindingKey]] = {}
    xhr_alias_bindings: set[BindingKey] = set()
    for alias_scope_key, name, value_node, destructured_member in alias_candidates:
        if destructured_member is not None:
            continue
        alias_binding = (alias_scope_key, name)
        if alias_binding not in immutable_bindings:
            continue
        alias_source = xhr_alias_source(value_node, value_node)
        if alias_source is direct_alias:
            xhr_alias_bindings.add(alias_binding)
        elif isinstance(alias_source, tuple) and alias_source in immutable_bindings:
            xhr_alias_dependants.setdefault(alias_source, set()).add(alias_binding)

    pending_bindings = list(xhr_alias_bindings)
    while pending_bindings:
        alias_binding = pending_bindings.pop()
        dependants = xhr_alias_dependants.get(alias_binding)
        if dependants is None:
            continue
        for dependant in dependants:
            if dependant not in xhr_alias_bindings:
                xhr_alias_bindings.add(dependant)
                pending_bindings.append(dependant)

    def is_xhr_instance_expression(expression: Node | None, use_node: Node) -> bool:
        active_bindings: set[BindingKey] = set()
        while expression is not None:
            alias_source = xhr_alias_source(expression, use_node)
            if alias_source is direct_alias:
                return True
            if not isinstance(alias_source, tuple):
                return False
            if alias_source in xhr_alias_bindings:
                return True
            if alias_source in active_bindings:
                return False
            active_bindings.add(alias_source)
            previous_value = binding_value_before(alias_source, use_node)
            if previous_value is None or previous_value[1] is not None:
                return False
            expression = previous_value[0]
            use_node = expression
        return False

    def callable_alias_source(
        expression: Node | None,
        use_node: Node,
        names: set[str],
        receivers: set[str | None],
    ) -> object | BindingKey | None:
        if expression is None:
            return None
        while expression.type == "parenthesized_expression":
            children = expression.named_children
            if len(children) != 1:
                return None
            expression = children[0]
        path = _javascript_static_path(source, expression)
        if path:
            receiver = ".".join(path[:-1]) or None
            member = path[-1]
            if member in names and receiver in receivers:
                if global_receiver_available(receiver, member, use_node):
                    return direct_alias
                if receiver is None:
                    dependency_scope = binding_scope(member, use_node)
                    return (dependency_scope, member) if dependency_scope is not None else None
                return None
            if len(path) == 1:
                dependency_scope = binding_scope(member, use_node)
                return (dependency_scope, member) if dependency_scope is not None else None
        if expression.type == "sequence_expression" and expression.named_children:
            return callable_alias_source(expression.named_children[-1], use_node, names, receivers)
        if expression.type == "call_expression":
            function = expression.child_by_field_name("function")
            bind_receiver, bind_member = _javascript_member(source, function)
            if bind_receiver is not None and bind_member == "bind" and function is not None:
                target = function.child_by_field_name("object")
                return callable_alias_source(target, use_node, names, receivers)
        return None

    def callable_destructured_source(
        expression: Node,
        use_node: Node,
        member: str,
        names: set[str],
        receivers: set[str | None],
    ) -> object | None:
        if member not in names:
            return None
        path = _javascript_static_path(source, expression)
        if not path:
            return None
        receiver = ".".join(path)
        if receiver not in receivers:
            return None
        root = path[0]
        return direct_alias if global_receiver_available(None, root, use_node) else None

    def build_callable_aliases(names: set[str], receivers: set[str | None]) -> set[BindingKey]:
        dependants: dict[BindingKey, set[BindingKey]] = {}
        resolved: set[BindingKey] = set()
        for alias_scope_key, name, value_node, destructured_member in alias_candidates:
            alias_binding = (alias_scope_key, name)
            if alias_binding not in immutable_bindings:
                continue
            alias_source: object | BindingKey | None
            if destructured_member is None:
                alias_source = callable_alias_source(value_node, value_node, names, receivers)
            else:
                alias_source = callable_destructured_source(
                    value_node,
                    value_node,
                    destructured_member,
                    names,
                    receivers,
                )
            if alias_source is direct_alias:
                resolved.add(alias_binding)
            elif isinstance(alias_source, tuple) and alias_source in immutable_bindings:
                dependants.setdefault(alias_source, set()).add(alias_binding)

        pending = list(resolved)
        while pending:
            alias_binding = pending.pop()
            for dependant in dependants.get(alias_binding, ()):
                if dependant not in resolved:
                    resolved.add(dependant)
                    pending.append(dependant)
        return resolved

    fetch_alias_bindings = build_callable_aliases({"fetch"}, fetch_contexts)
    import_scripts_alias_bindings = build_callable_aliases({"importScripts"}, worker_contexts)
    worker_alias_bindings = build_callable_aliases({"SharedWorker", "Worker"}, fetch_contexts)

    def is_callable_expression(
        expression: Node | None,
        use_node: Node,
        names: set[str],
        receivers: set[str | None],
        resolved: set[BindingKey],
    ) -> bool:
        active_bindings: set[BindingKey] = set()
        while expression is not None:
            alias_source = callable_alias_source(expression, use_node, names, receivers)
            if alias_source is direct_alias:
                return True
            if not isinstance(alias_source, tuple):
                return False
            if alias_source in resolved:
                return True
            if alias_source in active_bindings:
                return False
            active_bindings.add(alias_source)
            previous_value = binding_value_before(alias_source, use_node)
            if previous_value is None:
                return False
            expression, destructured_member = previous_value
            if destructured_member is not None:
                return (
                    callable_destructured_source(
                        expression,
                        expression,
                        destructured_member,
                        names,
                        receivers,
                    )
                    is direct_alias
                )
            use_node = expression
        return False

    def javascript_static_string(expression: Node | None) -> str | None:
        if expression is None:
            return None
        tasks: list[Node | BindingKey] = [expression]
        active_bindings: set[BindingKey] = set()
        fragments: list[str] = []
        total_length = 0
        while tasks:
            task = tasks.pop()
            if isinstance(task, tuple):
                active_bindings.remove(task)
                continue
            charge_scope_step()
            literal = _javascript_string_value(source, task)
            if literal is not None:
                fragments.append(literal)
                total_length += len(literal)
            elif task.type == "string_fragment":
                fragment = _javascript_template_fragment_value(source, task)
                fragments.append(fragment)
                total_length += len(fragment)
            elif task.type == "parenthesized_expression":
                children = task.named_children
                if len(children) != 1:
                    return None
                tasks.append(children[0])
            elif task.type == "binary_expression" and any(
                child.type == "+" for child in task.children
            ):
                left = task.child_by_field_name("left")
                right = task.child_by_field_name("right")
                if left is None or right is None:
                    return None
                tasks.extend((right, left))
            elif task.type == "template_string":
                template_parts: list[Node] = []
                for child in task.named_children:
                    if child.type == "string_fragment":
                        template_parts.append(child)
                    elif child.type == "template_substitution":
                        children = child.named_children
                        if len(children) != 1:
                            return None
                        template_parts.append(children[0])
                    else:
                        return None
                tasks.extend(reversed(template_parts))
            elif task.type == "identifier":
                name = _javascript_identifier_value(source, task)
                scope = binding_scope(name, task)
                binding = (scope, name) if scope is not None else None
                initializer = constant_string_bindings.get(binding) if binding is not None else None
                if binding is None or initializer is None or binding in active_bindings:
                    return None
                active_bindings.add(binding)
                tasks.append(binding)
                tasks.append(initializer)
            else:
                return None
            if total_length > _MAX_JAVASCRIPT_BYTES:
                raise RuntimeError("Invalid EPUB: JavaScript static string exceeds 2 MiB")
        return "".join(fragments)

    def browsing_open_argument(node: Node, arguments: list[Node]) -> Node | None:
        function = node.child_by_field_name("function")
        if is_browsing_open_expression(function, node):
            return arguments[0] if arguments else None
        if function is None or function.type not in {
            "member_expression",
            "subscript_expression",
        }:
            return None
        target = function.child_by_field_name("object")
        member_node = function.child_by_field_name(
            "property" if function.type == "member_expression" else "index"
        )
        if not is_browsing_open_expression(target, node) or member_node is None:
            return None
        invocation = (
            _javascript_identifier_value(source, member_node)
            if function.type == "member_expression"
            else javascript_static_string(member_node)
        )
        if invocation == "call":
            return arguments[1] if len(arguments) > 1 else None
        if invocation == "apply" and len(arguments) > 1:
            array = arguments[1]
            return (
                array.named_children[0] if array.type == "array" and array.named_children else None
            )
        return None

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type in {"import_statement", "export_statement"}:
            reference = _javascript_string_value(source, node.child_by_field_name("source"))
            if reference:
                add_reference(reference)
            continue
        if node.type == "new_expression":
            constructor = node.child_by_field_name("constructor")
            arguments_node = node.child_by_field_name("arguments")
            arguments = arguments_node.named_children if arguments_node is not None else []
            if (
                is_callable_expression(
                    constructor,
                    node,
                    {"SharedWorker", "Worker"},
                    fetch_contexts,
                    worker_alias_bindings,
                )
                and arguments
            ):
                reference = javascript_static_string(arguments[0])
                if reference:
                    add_reference(reference)
            continue
        if node.type != "call_expression":
            continue
        call_receiver, member = _javascript_member(source, node.child_by_field_name("function"))
        arguments_node = node.child_by_field_name("arguments")
        arguments = arguments_node.named_children if arguments_node is not None else []
        browsing_argument = browsing_open_argument(node, arguments)
        browsing_reference = javascript_static_string(browsing_argument)
        if browsing_reference:
            browsing_scheme = urlsplit(browsing_reference).scheme.lower()
            if browsing_scheme == "data":
                raise RuntimeError("Invalid EPUB: data URLs cannot open browsing contexts")
            if browsing_scheme == "file":
                raise RuntimeError("Invalid EPUB: file URLs are forbidden")
        function_node = node.child_by_field_name("function")
        if is_callable_expression(
            function_node,
            node,
            {"importScripts"},
            worker_contexts,
            import_scripts_alias_bindings,
        ):
            for argument in arguments:
                reference = javascript_static_string(argument)
                if reference:
                    add_reference(reference)
        elif (
            is_callable_expression(
                function_node,
                node,
                {"fetch"},
                fetch_contexts,
                fetch_alias_bindings,
            )
            or (member == "import" and call_receiver is None)
        ) and arguments:
            reference = javascript_static_string(arguments[0])
            if reference:
                add_reference(reference)
        xhr_instance = (
            function_node.child_by_field_name("object")
            if function_node is not None
            and function_node.type in {"member_expression", "subscript_expression"}
            else None
        )
        if member == "open" and arguments:
            if is_xhr_instance_expression(xhr_instance, node) and len(arguments) > 1:
                reference = javascript_static_string(arguments[1])
                if reference:
                    add_reference(reference)
    return references


def _resource_base_url(resource_path: str) -> str:
    encoded_path = quote(resource_path, safe="/")
    return f"https://{_INTERNAL_URL_HOST}/{encoded_path}"


def _document_base_url(root: etree.Element, resource_path: str) -> str:
    base_url = _resource_base_url(resource_path)
    if root.tag != _qualified(_XHTML_NAMESPACE, "html"):
        return base_url
    head = next(
        (child for child in root if child.tag == _qualified(_XHTML_NAMESPACE, "head")),
        None,
    )
    head_children = list(head) if head is not None else []
    base_element = next(
        (
            element
            for element in head_children
            if element.tag == _qualified(_XHTML_NAMESPACE, "base")
            and element.get("href", "").strip()
        ),
        None,
    )
    if base_element is None:
        return base_url
    base_reference = base_element.get("href", "")
    if "\\" in base_reference:
        raise RuntimeError("Invalid EPUB: reverse solidus in URL reference")
    parsed_reference = urlsplit(base_reference)
    if parsed_reference.scheme.lower() in {"http", "https"} and not parsed_reference.netloc:
        raise RuntimeError("Invalid EPUB: HTTP(S) base URL requires an authority")
    resolved_base = urljoin(base_url, base_reference)
    parsed_base = urlsplit(resolved_base)
    if parsed_base.scheme.lower() == "file":
        raise RuntimeError("Invalid EPUB: file URLs are forbidden")
    if parsed_base.scheme.lower() in {"http", "https"} and not parsed_base.netloc:
        raise RuntimeError("Invalid EPUB: HTTP(S) base URL requires an authority")
    return resolved_base


def _resolved_resource_reference(base_url: str, reference: str) -> tuple[str, bool] | None:
    if "\\" in reference:
        raise RuntimeError("Invalid EPUB: reverse solidus in URL reference")
    parsed_reference = urlsplit(reference)
    if parsed_reference.scheme.lower() in {"http", "https"} and not parsed_reference.netloc:
        raise RuntimeError(f"Invalid EPUB: HTTP(S) resource URL requires an authority: {reference}")
    resolved = urlsplit(urljoin(base_url, reference))
    if resolved.scheme.lower() == "file":
        raise RuntimeError("Invalid EPUB: file URLs are forbidden")
    if resolved.scheme.lower() == "data":
        # A data URL is embedded in its containing EPUB resource and therefore
        # has no separate manifest item. Callers reject only the top-level
        # browsing contexts prohibited by EPUB 3.3 section 3.7.
        return None
    if (
        resolved.scheme.lower() == "about"
        and not resolved.netloc
        and resolved.path in {"blank", "srcdoc"}
    ):
        return None
    if resolved.scheme.lower() not in {"http", "https"}:
        raise RuntimeError(f"Invalid EPUB: unsupported resource URL scheme: {reference}")
    if not resolved.netloc:
        raise RuntimeError(f"Invalid EPUB: HTTP(S) resource URL requires an authority: {reference}")
    if resolved.scheme in {"http", "https"} and resolved.hostname != _INTERNAL_URL_HOST:
        return _remote_key(resolved.geturl()), True
    if resolved.scheme in {"http", "https"} and resolved.hostname == _INTERNAL_URL_HOST:
        decoded = unquote(resolved.path.lstrip("/"))
        if not decoded or decoded.startswith(("/", "\\")) or "\\" in decoded:
            raise RuntimeError(f"Invalid EPUB: unsafe resource reference: {reference}")
        target = posixpath.normpath(decoded)
        if target == ".." or target.startswith("../"):
            raise RuntimeError(f"Invalid EPUB: unsafe resource reference: {reference}")
        return target, False
    raise RuntimeError(f"Invalid EPUB: invalid HTTP(S) resource URL: {reference}")


def _classified_references(
    references: set[str], kind: ReferenceKind, base_url: str
) -> set[ResourceReference]:
    classified: set[ResourceReference] = set()
    for reference in references:
        resolved = _resolved_resource_reference(base_url, reference)
        if resolved is not None:
            target, remote = resolved
            classified.add((target, kind, remote))
    return classified


def _is_resource_reference_attribute(
    element_namespace: str,
    element_name: str,
    attribute_namespace: str,
    attribute_name: str,
) -> bool:
    if attribute_namespace:
        return (
            element_namespace == _SVG_NAMESPACE
            and attribute_namespace == _XLINK_NAMESPACE
            and attribute_name == "href"
            and element_name != "a"
        )
    if element_namespace == _XHTML_NAMESPACE:
        return attribute_name in _XHTML_RESOURCE_ATTRIBUTES.get(element_name, _EMPTY_ATTRIBUTES)
    if element_namespace == _SVG_NAMESPACE:
        return attribute_name == "href" and element_name != "a"
    if element_namespace == _SMIL_NAMESPACE:
        return attribute_name == "src" and element_name in {
            "audio",
            "img",
            "ref",
            "text",
            "textstream",
            "video",
        }
    if element_namespace == _NCX_NAMESPACE:
        return attribute_name == "src" and element_name in {"audio", "content", "img"}
    if element_namespace == _DTBOOK_NAMESPACE:
        return attribute_name in _DTBOOK_RESOURCE_ATTRIBUTES.get(element_name, _EMPTY_ATTRIBUTES)
    if element_namespace == _MATHML_NAMESPACE:
        return element_name == "mglyph" and attribute_name == "src"
    return False


def _can_contain_remote_reference(media_type: str) -> bool:
    return (
        media_type == "text/css"
        or media_type in _JAVASCRIPT_MEDIA_TYPES
        or media_type in _XML_REFERENCE_MEDIA_TYPES
    )


def _processed_script_references(value: str, script_type: str) -> set[str]:
    if len(value.encode("utf-8")) > _MAX_JAVASCRIPT_BYTES:
        raise RuntimeError("Invalid EPUB: processed script exceeds 2 MiB")
    try:
        payload: object = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        # Browsers report invalid import maps and speculation rules without
        # processing their contents, so malformed JSON cannot initiate a load.
        return set()
    if not isinstance(payload, dict):
        return set()

    references: set[str] = set()

    def add(reference: object) -> None:
        if not isinstance(reference, str):
            return
        if reference not in references and len(references) >= _MAX_REFERENCES_PER_RESOURCE:
            raise RuntimeError("Invalid EPUB: resource reference budget exceeded")
        references.add(reference)

    if script_type == "importmap":
        imports = payload.get("imports")
        if isinstance(imports, dict):
            for target in imports.values():
                add(target)
        scopes = payload.get("scopes")
        if isinstance(scopes, dict):
            for mappings in scopes.values():
                if isinstance(mappings, dict):
                    for target in mappings.values():
                        add(target)
        integrity = payload.get("integrity")
        if isinstance(integrity, dict):
            for target in integrity:
                add(target)
    elif script_type == "speculationrules":
        for operation in ("prefetch", "prerender"):
            rules = payload.get(operation)
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                urls = rule.get("urls")
                if isinstance(urls, list):
                    for target in urls:
                        add(target)
    return references


def _resource_remote_references(
    value: bytes,
    media_type: str,
    resource_path: str,
    javascript_budget: JavaScriptBudget,
) -> ResourceScan:
    if media_type == "text/css":
        encoding = "utf-16" if value.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = value.decode(encoding, "replace")
        base_url = _resource_base_url(resource_path)
        return _classified_references(_css_references(text), "embedded", base_url), False
    if media_type in _JAVASCRIPT_MEDIA_TYPES:
        if len(value) > _MAX_JAVASCRIPT_BYTES:
            raise RuntimeError("Invalid EPUB: JavaScript resource exceeds 2 MiB")
        text = value.decode("utf-8-sig", "replace")
        base_url = _resource_base_url(resource_path)
        return _classified_references(
            _script_remote_references(text, javascript_budget), "script", base_url
        ), False
    if media_type not in _XML_REFERENCE_MEDIA_TYPES:
        return set(), False
    chunks = (value[index : index + 64 * 1024] for index in range(0, len(value), 64 * 1024))
    root = _bounded_xml_root(chunks, f"resource: {resource_path}")
    references: set[ResourceReference] = set()
    scripted_content = False
    content_document = root.tag in {
        _qualified(_XHTML_NAMESPACE, "html"),
        _qualified(_SVG_NAMESPACE, "svg"),
    }
    document_base_url = _document_base_url(root, resource_path)

    def add_reference(reference: str, kind: ReferenceKind, base_url: str) -> None:
        resolved = _resolved_resource_reference(base_url, reference)
        if resolved is not None:
            target, remote = resolved
            classified = (target, kind, remote)
            if classified not in references and len(references) >= _MAX_REFERENCES_PER_RESOURCE:
                raise RuntimeError("Invalid EPUB: resource reference budget exceeded")
            references.add(classified)

    pending: list[tuple[etree.Element, str, bool]] = [(root, document_base_url, False)]
    while pending:
        element, inherited_base_url, inside_iframe = pending.pop()
        element_base_url = urljoin(inherited_base_url, element.get(_XML_BASE, ""))
        element_name = _local_name(element.tag)
        element_namespace = _namespace(element.tag)
        child_inside_iframe = inside_iframe or (
            element_namespace == _XHTML_NAMESPACE and element_name == "iframe"
        )
        # WHATWG applies JavaScript MIME *essence matching* directly to this
        # stripped attribute; parameters are significant here, so for example
        # `text/javascript; charset=utf-8` is a non-executable data block.
        # https://html.spec.whatwg.org/multipage/scripting.html#scriptingLanguages
        script_type = element.get("type", "").strip().lower()
        script_type_match = (
            script_type.split(";", 1)[0].strip()
            if _namespace(element.tag) == _SVG_NAMESPACE
            else script_type
        )
        executable_script = element_name != "script" or (
            not script_type_match
            or script_type_match == "module"
            or script_type_match in _JAVASCRIPT_MEDIA_TYPES
        )
        processed_script_type = (
            element_namespace == _XHTML_NAMESPACE
            and element_name == "script"
            and script_type_match in {"importmap", "speculationrules"}
        )
        if content_document and (
            element_name == "form"
            or (element_name == "script" and (executable_script or processed_script_type))
        ):
            scripted_content = True
        for name, attribute in element.attrib.items():
            local_name = _local_name(name)
            attribute_namespace = _namespace(name)
            if (
                not attribute_namespace
                and element_namespace == _XHTML_NAMESPACE
                and element_name == "iframe"
                and local_name == "srcdoc"
            ):
                raise RuntimeError("Invalid EPUB: iframe srcdoc cannot be validated safely")
            resource_attribute = _is_resource_reference_attribute(
                element_namespace,
                element_name,
                attribute_namespace,
                local_name,
            )
            srcset_attribute = (
                not attribute_namespace
                and element_namespace == _XHTML_NAMESPACE
                and (
                    (local_name == "srcset" and element_name in {"img", "source"})
                    or (local_name == "imagesrcset" and element_name == "link")
                )
            )
            hyperlink_attribute = (
                not attribute_namespace
                and local_name == "href"
                and (
                    (
                        element_name in {"a", "area", "base"}
                        and element_namespace == _XHTML_NAMESPACE
                    )
                    or (element_name == "a" and element_namespace == _SVG_NAMESPACE)
                )
            )
            event_handler = (
                content_document
                and not attribute_namespace
                and len(local_name) > 2
                and local_name.lower().startswith("on")
            )
            if event_handler:
                scripted_content = True
                for reference in _script_remote_references(attribute, javascript_budget):
                    add_reference(reference, "script", element_base_url)
            if (
                not attribute_namespace
                and element_namespace == _SVG_NAMESPACE
                and local_name in _SVG_URL_PRESENTATION_ATTRIBUTES
            ):
                for reference in _css_references(attribute):
                    add_reference(reference, "embedded", element_base_url)
            if (
                resource_attribute or srcset_attribute or hyperlink_attribute
            ) and "\\" in attribute:
                raise RuntimeError("Invalid EPUB: reverse solidus in URL reference")
            if (
                hyperlink_attribute
                and element_name in {"a", "area"}
                and (
                    (
                        element_name == "a"
                        and element_namespace in {_XHTML_NAMESPACE, _SVG_NAMESPACE}
                    )
                    or (element_name == "area" and element_namespace == _XHTML_NAMESPACE)
                )
                and not inside_iframe
                and urlsplit(urljoin(element_base_url, attribute)).scheme.lower() == "data"
            ):
                raise RuntimeError("Invalid EPUB: data URLs cannot open browsing contexts")
            if (hyperlink_attribute or resource_attribute) and urlsplit(
                urljoin(element_base_url, attribute)
            ).scheme.lower() == "file":
                raise RuntimeError("Invalid EPUB: file URLs are forbidden")
            if (
                resource_attribute
                and executable_script
                and (
                    not (element_namespace == _XHTML_NAMESPACE and element_name == "link")
                    or bool(set(element.get("rel", "").lower().split()) & _FETCHING_LINK_RELATIONS)
                )
            ):
                add_reference(attribute, "embedded", element_base_url)
            elif srcset_attribute:
                for reference in _srcset_references(attribute):
                    add_reference(reference, "embedded", element_base_url)
            elif (
                not attribute_namespace
                and local_name == "style"
                and element_namespace in {_XHTML_NAMESPACE, _SVG_NAMESPACE}
            ):
                for reference in _css_references(attribute):
                    add_reference(reference, "embedded", element_base_url)
        if element_name == "style" and element_namespace in {
            _XHTML_NAMESPACE,
            _SVG_NAMESPACE,
        }:
            for reference in _css_references(element.text or ""):
                add_reference(reference, "embedded", element_base_url)
        elif (
            element_name == "script"
            and element_namespace in {_XHTML_NAMESPACE, _SVG_NAMESPACE}
            and executable_script
        ):
            for reference in _script_remote_references(element.text or "", javascript_budget):
                add_reference(reference, "script", element_base_url)
        elif processed_script_type:
            for reference in _processed_script_references(element.text or "", script_type_match):
                add_reference(reference, "script", element_base_url)
        pending.extend(
            (child, element_base_url, child_inside_iframe) for child in reversed(list(element))
        )
    return references, scripted_content


def _remote_referrers(
    archive: zipfile.ZipFile,
    items: dict[str, ManifestItem],
    item_targets: dict[str, str],
    parsed_resources: ParsedResources,
    javascript_budget: JavaScriptBudget,
    reference_cache: RemoteReferenceCache,
) -> tuple[dict[str, dict[str, set[ReferenceKind]]], tuple[str, str] | None]:
    remote_targets = {
        target for item_id, target in item_targets.items() if items[item_id][0] is None
    }
    manifest_targets = set(item_targets.values())
    referrers: dict[str, dict[str, set[ReferenceKind]]] = {target: {} for target in remote_targets}
    for item_id, (resource_path, media_type, properties, _) in items.items():
        if resource_path is None or not _can_contain_remote_reference(media_type):
            continue
        _claim_parsed_resource(archive, resource_path, parsed_resources)
        scan_type = (
            "css"
            if media_type == "text/css"
            else "javascript"
            if media_type in _JAVASCRIPT_MEDIA_TYPES
            else "xml"
        )
        cache_key = (resource_path, scan_type)
        if cache_key not in reference_cache:
            if (
                scan_type == "javascript"
                and archive.getinfo(resource_path).file_size > _MAX_JAVASCRIPT_BYTES
            ):
                raise RuntimeError("Invalid EPUB: JavaScript resource exceeds 2 MiB")
            reference_cache[cache_key] = _resource_remote_references(
                archive.read(resource_path),
                media_type,
                resource_path,
                javascript_budget,
            )
        resource_references, scripted_content = reference_cache[cache_key]
        if scripted_content and "scripted" not in properties:
            raise RuntimeError(
                f"Invalid EPUB: scripted content is missing the scripted property: {item_id}"
            )
        for target, kind, remote in resource_references:
            if target not in manifest_targets:
                location = "remote" if remote else "local"
                raise RuntimeError(
                    f"Invalid EPUB: {location} resource is missing from the manifest: "
                    f"{target} (referenced by {item_id})"
                )
            if not remote:
                continue
            if target in referrers:
                referrers[target].setdefault(item_id, set()).add(kind)
            else:
                return referrers, (item_id, target)
    return referrers, None


def _validate_remote_resources(
    archive: zipfile.ZipFile,
    items: dict[str, ManifestItem],
    item_targets: dict[str, str],
    epub3: bool,
    parsed_resources: ParsedResources,
    javascript_budget: JavaScriptBudget,
    reference_cache: RemoteReferenceCache,
) -> None:
    dynamic_script_referrers = {
        item_id
        for item_id, (resource_path, media_type, properties, _) in items.items()
        if resource_path is not None
        and "remote-resources" in properties
        and ("scripted" in properties or media_type in _JAVASCRIPT_MEDIA_TYPES)
    }
    opaque_xml_referrers = {
        item_id
        for item_id, (resource_path, media_type, properties, _) in items.items()
        if resource_path is not None
        and "remote-resources" in properties
        and (media_type == "application/xml" or media_type.endswith("+xml"))
        and media_type not in _XML_REFERENCE_MEDIA_TYPES
    }
    detected_referrers, missing_manifest = _remote_referrers(
        archive,
        items,
        item_targets,
        parsed_resources,
        javascript_budget,
        reference_cache,
    )
    if missing_manifest is not None:
        referrer, reference = missing_manifest
        raise RuntimeError(
            f"Invalid EPUB: remote resource is missing from the manifest: "
            f"{reference} (referenced by {referrer})"
        )
    for item_id, (resource_path, media_type, _, _) in items.items():
        if resource_path is not None:
            continue
        target = item_targets[item_id]
        referrers = detected_referrers[target]
        has_opaque_referrer = bool(dynamic_script_referrers or opaque_xml_referrers)
        if (not referrers and not has_opaque_referrer) or any(
            "remote-resources" not in items[referrer][2] for referrer in referrers
        ):
            raise RuntimeError(
                f"Invalid EPUB: remote resource lacks a referring declaration: {item_id}"
            )
        directly_allowed = media_type.startswith(("audio/", "video/", "font/")) or (
            media_type in _REMOTE_FONT_MEDIA_TYPES
        )
        script_retrieved = (
            bool(dynamic_script_referrers)
            if not referrers
            else all(
                kinds == {"script"}
                and (
                    items[referrer][1] in _JAVASCRIPT_MEDIA_TYPES
                    or "scripted" in items[referrer][2]
                )
                for referrer, kinds in referrers.items()
            )
        )
        if not epub3 or not (directly_allowed or script_retrieved):
            raise RuntimeError(
                f"Invalid EPUB: remote manifest resource is not permitted: {item_id}"
            )


def _direct_children(element: etree.Element, name: str) -> list[etree.Element]:
    expected = _qualified(_namespace(element.tag), name)
    return [child for child in element if child.tag == expected]


def _element_base_urls(root: etree.Element, document_base_url: str) -> dict[int, str]:
    base_urls: dict[int, str] = {}
    pending: list[tuple[etree.Element, str]] = [(root, document_base_url)]
    while pending:
        element, inherited_base_url = pending.pop()
        element_base_url = urljoin(inherited_base_url, element.get(_XML_BASE, ""))
        base_urls[id(element)] = element_base_url
        pending.extend((child, element_base_url) for child in reversed(list(element)))
    return base_urls


def _has_navigation_label(element: etree.Element) -> bool:
    non_textual = {
        "audio",
        "canvas",
        "embed",
        "iframe",
        "img",
        "math",
        "object",
        "picture",
        "svg",
        "video",
    }
    label_parts = [element.get("title", ""), *element.itertext()]
    for descendant in element.iter():
        if descendant is element or _local_name(descendant.tag) not in non_textual:
            continue
        label_parts.extend(
            value for name in ("title", "alt") if (value := descendant.get(name, ""))
        )
    return bool(re.sub(r"\s+", " ", "".join(label_parts)).strip())


def _validate_epub3_navigation_list(
    ordered_list: etree.Element,
    base_urls: dict[int, str],
    require_link_type: bool = False,
) -> list[NavigationReference]:
    entries = _direct_children(ordered_list, "li")
    if not entries:
        raise RuntimeError("Invalid EPUB: navigation table of contents is empty")
    targets: list[NavigationReference] = []
    pending = list(reversed(entries))
    while pending:
        entry = pending.pop()
        children = list(entry)
        label_names = {
            _qualified(_XHTML_NAMESPACE, "a"),
            _qualified(_XHTML_NAMESPACE, "span"),
        }
        if (
            not children
            or children[0].tag not in label_names
            or len(children) > 2
            or (len(children) == 2 and children[1].tag != _qualified(_XHTML_NAMESPACE, "ol"))
        ):
            raise RuntimeError("Invalid EPUB: malformed navigation table of contents entry")
        label = children[0]
        if not _has_navigation_label(label) or (
            label.tag == _qualified(_XHTML_NAMESPACE, "a") and not label.get("href", "").strip()
        ):
            raise RuntimeError("Invalid EPUB: unlabeled navigation table of contents entry")
        if label.tag == _qualified(_XHTML_NAMESPACE, "span") and len(children) != 2:
            raise RuntimeError("Invalid EPUB: navigation span must contain a subsidiary list")
        if label.tag == _qualified(_XHTML_NAMESPACE, "a"):
            if (
                require_link_type
                and not label.get("{http://www.idpf.org/2007/ops}type", "").strip()
            ):
                raise RuntimeError("Invalid EPUB: landmark navigation link has no type")
            targets.append((label.get("href", ""), base_urls[id(label)]))
        if len(children) == 2:
            nested_entries = _direct_children(children[1], "li")
            if not nested_entries:
                raise RuntimeError("Invalid EPUB: navigation table of contents is empty")
            pending.extend(reversed(nested_entries))
    return targets


def _validate_specialized_navigation(
    navigation: etree.Element,
    base_urls: dict[int, str],
    require_link_type: bool = False,
) -> list[NavigationReference]:
    children = list(navigation)
    valid_heading = children and children[0].tag in {
        _qualified(_XHTML_NAMESPACE, name) for name in ("h1", "h2", "h3", "h4", "h5", "h6")
    }
    ordered_list_index = 1 if valid_heading else 0
    if len(children) != ordered_list_index + 1 or children[ordered_list_index].tag != _qualified(
        _XHTML_NAMESPACE, "ol"
    ):
        raise RuntimeError("Invalid EPUB: malformed specialized navigation content")
    return _validate_epub3_navigation_list(
        children[ordered_list_index], base_urls, require_link_type
    )


def _validate_epub3_navigation(
    root: etree.Element, document_base_url: str
) -> list[NavigationReference]:
    if root.tag != _qualified(_XHTML_NAMESPACE, "html"):
        raise RuntimeError("Invalid EPUB: malformed XHTML navigation document")
    typed_navigation = [
        (
            element,
            set(element.get("{http://www.idpf.org/2007/ops}type", "").split()),
        )
        for element in root.iter()
        if element.tag == _qualified(_XHTML_NAMESPACE, "nav")
        and element.get("{http://www.idpf.org/2007/ops}type", "").strip()
    ]
    if sum("toc" in types for _, types in typed_navigation) != 1:
        raise RuntimeError("Invalid EPUB: navigation document must contain one table of contents")
    for unique_type in ("page-list", "landmarks"):
        if sum(unique_type in types for _, types in typed_navigation) > 1:
            raise RuntimeError(f"Invalid EPUB: multiple {unique_type} navigation elements")
    targets: list[NavigationReference] = []
    base_urls = _element_base_urls(root, document_base_url)
    for navigation, types in typed_navigation:
        navigation_targets = _validate_specialized_navigation(
            navigation, base_urls, require_link_type="landmarks" in types
        )
        if types.intersection({"toc", "page-list", "landmarks"}):
            targets.extend(navigation_targets)
    return targets


def _validate_epub2_navigation(
    root: etree.Element, document_base_url: str
) -> list[NavigationReference]:
    if root.tag != _qualified(_NCX_NAMESPACE, "ncx"):
        raise RuntimeError("Invalid EPUB: malformed EPUB2 NCX navigation document")
    maps = [
        element for element in root.iter() if element.tag == _qualified(_NCX_NAMESPACE, "navMap")
    ]
    if len(maps) != 1:
        raise RuntimeError("Invalid EPUB: EPUB2 NCX must contain one navigation map")
    points = [
        element
        for element in maps[0].iter()
        if element.tag == _qualified(_NCX_NAMESPACE, "navPoint")
    ]
    if not points:
        raise RuntimeError("Invalid EPUB: EPUB2 NCX navigation map is empty")
    targets: list[NavigationReference] = []
    base_urls = _element_base_urls(root, document_base_url)
    for point in points:
        labels = _direct_children(point, "navLabel")
        contents = _direct_children(point, "content")
        has_label = any("".join(label.itertext()).strip() for label in labels)
        if not has_label or len(contents) != 1 or not contents[0].get("src", "").strip():
            raise RuntimeError("Invalid EPUB: malformed EPUB2 NCX navigation point")
        content = contents[0]
        targets.append((content.get("src", ""), base_urls[id(content)]))
    return targets


def _navigation_target(base_url: str, href: str) -> tuple[str, str | None]:
    reference = urlsplit(urljoin(base_url, href))
    if reference.scheme not in {"http", "https"} or not reference.netloc:
        raise RuntimeError(f"Invalid EPUB: unsafe navigation target: {href}")
    fragment = unquote(reference.fragment) or None
    if reference.hostname != _INTERNAL_URL_HOST:
        return reference._replace(fragment="").geturl(), None
    decoded = unquote(reference.path.lstrip("/"))
    if not decoded or decoded.startswith(("/", "\\")) or "\\" in decoded:
        raise RuntimeError(f"Invalid EPUB: unsafe navigation target: {href}")
    target = posixpath.normpath(decoded)
    if target == ".." or target.startswith("../"):
        raise RuntimeError(f"Invalid EPUB: unsafe navigation target: {href}")
    return target, fragment


def _spine_chain_ids(
    items: dict[str, ManifestItem],
    item_id: str,
    cache: dict[str, tuple[str, ...]],
    fallback_budget: list[int],
) -> tuple[str, ...]:
    cached = cache.get(item_id)
    if cached is not None:
        return cached
    chain: list[str] = []
    current = item_id
    visited: set[str] = set()
    while current not in visited:
        fallback_budget[0] += 1
        if fallback_budget[0] > _MAX_SPINE_FALLBACK_STEPS:
            raise RuntimeError("Invalid EPUB: spine fallback traversal budget exceeded")
        visited.add(current)
        chain.append(current)
        fallback = items[current][3]
        if fallback is None:
            break
        current = fallback
    result = tuple(chain)
    cache[item_id] = result
    return result


def _validate_package(
    archive: zipfile.ZipFile,
    names: set[str],
    package_path: str,
    parsed_resources: ParsedResources,
    javascript_budget: JavaScriptBudget,
    reference_cache: RemoteReferenceCache,
) -> str:
    _claim_parsed_resource(archive, package_path, parsed_resources)
    package = _xml_root(archive, package_path, "OPF package")
    version = str(package.get("version", ""))
    if package.tag != _qualified(_OPF_NAMESPACE, "package") or version not in {
        "2.0",
        "3.0",
        "3.1",
    }:
        raise RuntimeError("Invalid EPUB: expected an EPUB2 or EPUB3 OPF package")
    epub3 = version in {"3.0", "3.1"}
    package_base_urls = _element_base_urls(package, _resource_base_url(package_path))
    for element in package.iter():
        if element.tag != _qualified(_OPF_NAMESPACE, "link"):
            continue
        href = element.get("href", "")
        scheme = urlsplit(urljoin(package_base_urls[id(element)], href)).scheme.lower()
        if scheme == "data":
            raise RuntimeError("Invalid EPUB: data URLs are forbidden in package links")
        if scheme == "file":
            raise RuntimeError("Invalid EPUB: file URLs are forbidden")
    manifest = next(
        (child for child in package if child.tag == _qualified(_OPF_NAMESPACE, "manifest")),
        None,
    )
    spine = next(
        (child for child in package if child.tag == _qualified(_OPF_NAMESPACE, "spine")),
        None,
    )
    if manifest is None or spine is None:
        raise RuntimeError("Invalid EPUB: OPF package must contain a manifest and spine")

    items: dict[str, ManifestItem] = {}
    item_targets: dict[str, str] = {}
    seen_targets: set[str] = set()
    for item in manifest:
        if item.tag != _qualified(_OPF_NAMESPACE, "item"):
            continue
        item_id = item.get("id", "")
        href = item.get("href", "")
        media_type = item.get("media-type", "")
        if not item_id or not href or not media_type or item_id in items:
            raise RuntimeError("Invalid EPUB: malformed or duplicate OPF manifest item")
        resource_path = _archive_reference(package_path, href)
        if resource_path is not None and resource_path not in names:
            raise RuntimeError(f"Invalid EPUB: missing manifest resource: {resource_path}")
        fallback = item.get("fallback")
        target = resource_path if resource_path is not None else _remote_key(href)
        if target in seen_targets:
            raise RuntimeError("Invalid EPUB: duplicate OPF manifest resource")
        seen_targets.add(target)
        items[item_id] = (
            resource_path,
            media_type,
            set(item.get("properties", "").split()),
            fallback if fallback else None,
        )
        item_targets[item_id] = target
    if not items:
        raise RuntimeError("Invalid EPUB: empty OPF manifest")
    _validate_fallback_chains(items)

    navigation_path: str
    navigation_targets: list[NavigationReference]
    if epub3:
        navigation_items = [item for item in items.values() if "nav" in item[2]]
        if len(navigation_items) != 1:
            raise RuntimeError("Invalid EPUB: package must declare exactly one navigation document")
        navigation = navigation_items[0]
        if navigation[0] is None or navigation[1] != "application/xhtml+xml":
            raise RuntimeError("Invalid EPUB: missing XHTML navigation document")
        navigation_path = navigation[0]
        _claim_parsed_resource(archive, navigation_path, parsed_resources)
        navigation_root = _xml_root(archive, navigation_path, "navigation document")
        navigation_base_url = _document_base_url(navigation_root, navigation_path)
        navigation_targets = _validate_epub3_navigation(navigation_root, navigation_base_url)
    else:
        toc_id = spine.get("toc", "")
        ncx = items.get(toc_id)
        if ncx is None or ncx[0] is None or ncx[1] != "application/x-dtbncx+xml":
            raise RuntimeError("Invalid EPUB: missing EPUB2 NCX navigation document")
        navigation_path = ncx[0]
        _claim_parsed_resource(archive, navigation_path, parsed_resources)
        ncx_root = _xml_root(archive, navigation_path, "NCX navigation document")
        navigation_base_url = _resource_base_url(navigation_path)
        navigation_targets = _validate_epub2_navigation(ncx_root, navigation_base_url)

    itemrefs = [item for item in spine if item.tag == _qualified(_OPF_NAMESPACE, "itemref")]
    if not itemrefs:
        raise RuntimeError("Invalid EPUB: empty OPF spine")
    top_level_content: set[str] = set()
    content_fragments: dict[str, set[str]] = {}
    content_media_types: dict[str, str] = {}
    spine_chain_cache: dict[str, tuple[str, ...]] = {}
    processed_spine_items: set[str] = set()
    fallback_budget = [0]
    for itemref in itemrefs:
        item_id = itemref.get("idref", "")
        if item_id not in items:
            raise RuntimeError(f"Invalid EPUB: spine references unknown manifest item: {item_id}")
        if item_id in processed_spine_items:
            continue
        processed_spine_items.add(item_id)
        allowed_spine_types = (
            {"application/xhtml+xml", "image/svg+xml"}
            if epub3
            else {
                "application/xhtml+xml",
                "application/x-dtbook+xml",
                "text/x-oeb1-document",
            }
        )
        chain_ids = _spine_chain_ids(items, item_id, spine_chain_cache, fallback_budget)
        if not any(
            items[chain_id][0] is not None and items[chain_id][1] in allowed_spine_types
            for chain_id in chain_ids
        ):
            raise RuntimeError(
                f"Invalid EPUB: spine item has no supported local fallback: {item_id}"
            )
        top_level_content.update(item_targets[chain_id] for chain_id in chain_ids)
        for chain_id in chain_ids:
            resource_path, media_type, _, _ = items[chain_id]
            if (
                resource_path is None
                or media_type not in allowed_spine_types
                or resource_path in content_fragments
            ):
                continue
            _claim_parsed_resource(archive, resource_path, parsed_resources)
            expected_root = {
                "application/xhtml+xml": (
                    "XHTML",
                    _qualified(_XHTML_NAMESPACE, "html"),
                ),
                "text/x-oeb1-document": (
                    "XHTML",
                    _qualified(_XHTML_NAMESPACE, "html"),
                ),
                "image/svg+xml": ("SVG", _qualified(_SVG_NAMESPACE, "svg")),
                "application/x-dtbook+xml": (
                    "DTBook",
                    _qualified(_DTBOOK_NAMESPACE, "dtbook"),
                ),
            }[media_type]
            content_root = _xml_root(archive, resource_path, f"{expected_root[0]} content document")
            if content_root.tag != expected_root[1]:
                raise RuntimeError(
                    f"Invalid EPUB: malformed {expected_root[0]} content: {resource_path}"
                )
            identifiers: set[str] = set()
            for element in content_root.iter():
                identifier = element.get("id") or element.get(
                    "{http://www.w3.org/XML/1998/namespace}id"
                )
                if not identifier:
                    continue
                if identifier in identifiers:
                    raise RuntimeError(
                        f"Invalid EPUB: duplicate content identifier in {resource_path}: "
                        f"{identifier}"
                    )
                identifiers.add(identifier)
            content_fragments[resource_path] = identifiers
            content_media_types[resource_path] = media_type
    for href, base_url in navigation_targets:
        target, fragment = _navigation_target(base_url, href)
        if target not in top_level_content:
            raise RuntimeError(f"Invalid EPUB: navigation target is not in the spine: {href}")
        if (
            fragment
            and target in content_fragments
            and content_media_types[target] != "image/svg+xml"
            and fragment not in content_fragments[target]
        ):
            raise RuntimeError(f"Invalid EPUB: navigation fragment does not exist: {href}")
    _validate_remote_resources(
        archive,
        items,
        item_targets,
        epub3,
        parsed_resources,
        javascript_budget,
        reference_cache,
    )
    return version


def internal_validate(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise RuntimeError("Output is not a ZIP-based EPUB")
    with zipfile.ZipFile(path) as archive:
        parsed_resources: ParsedResources = {}
        javascript_budget: JavaScriptBudget = [0, 0]
        reference_cache: RemoteReferenceCache = {}
        names = archive.namelist()
        name_set = set(names)
        if len(names) != len(name_set):
            raise RuntimeError("Invalid EPUB: duplicate ZIP entries")
        canonical_names = {
            "/".join(
                unicodedata.normalize("NFC", unicodedata.normalize("NFC", component).casefold())
                for component in name.split("/")
            )
            for name in names
        }
        if len(canonical_names) != len(names):
            raise RuntimeError("Invalid EPUB: canonically colliding ZIP entries")
        if not names or names[0] != "mimetype":
            raise RuntimeError("Invalid EPUB: mimetype must be the first ZIP entry")
        mimetype = archive.getinfo("mimetype")
        if mimetype.compress_type != zipfile.ZIP_STORED:
            raise RuntimeError("Invalid EPUB: mimetype entry must be uncompressed")
        expected_mimetype = b"application/epub+zip"
        if (
            mimetype.file_size != len(expected_mimetype)
            or archive.read("mimetype") != expected_mimetype
        ):
            raise RuntimeError("Invalid EPUB mimetype")
        archive_entries = archive.infolist()
        if any(
            entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            for entry in archive_entries
        ):
            raise RuntimeError("Invalid EPUB: unsupported ZIP compression method")
        if len(names) > 10_000 or sum(item.file_size for item in archive_entries) > 2_000_000_000:
            raise RuntimeError("Unsafe EPUB archive size")
        if any(
            name.startswith(("/", "\\")) or "\\" in name or ".." in PurePosixPath(name).parts
            for name in names
        ):
            raise RuntimeError("Unsafe path found in EPUB archive")
        container_path = "META-INF/container.xml"
        if container_path not in name_set:
            raise RuntimeError("Invalid EPUB: missing META-INF/container.xml")
        _claim_parsed_resource(archive, container_path, parsed_resources)
        container = _xml_root(archive, container_path, "META-INF/container.xml")
        if container.tag != _qualified(_OCF_NAMESPACE, "container"):
            raise RuntimeError("Invalid EPUB: malformed META-INF/container.xml")
        rootfiles = [
            rootfile
            for rootfile in container.iter()
            if rootfile.tag == _qualified(_OCF_NAMESPACE, "rootfile")
        ]
        if not rootfiles:
            raise RuntimeError("Invalid EPUB: container has no OPF rootfile")
        package_media_type = "application/oebps-package+xml"
        primary = rootfiles[0]
        if primary.get("media-type") != package_media_type:
            raise RuntimeError("Invalid EPUB: primary rootfile is not an OPF package")
        primary_path = _archive_reference("", primary.get("full-path", ""))
        if primary_path is None or primary_path not in name_set:
            raise RuntimeError(f"Invalid EPUB: missing OPF package: {primary_path}")
        primary_version = _validate_package(
            archive,
            name_set,
            primary_path,
            parsed_resources,
            javascript_budget,
            reference_cache,
        )
        package_versions = {primary_path: primary_version}
        for rootfile in rootfiles[1:]:
            package_path = _archive_reference("", rootfile.get("full-path", ""))
            if package_path is None or package_path not in name_set:
                label = (
                    "OPF package"
                    if rootfile.get("media-type") == package_media_type
                    else "rootfile resource"
                )
                raise RuntimeError(f"Invalid EPUB: missing {label}: {package_path}")
            if primary_version == "2.0" and rootfile.get("media-type") != package_media_type:
                continue
            if rootfile.get("media-type") != package_media_type:
                raise RuntimeError("Invalid EPUB: rootfile has an invalid media type")
            if package_path not in package_versions:
                package_versions[package_path] = _validate_package(
                    archive,
                    name_set,
                    package_path,
                    parsed_resources,
                    javascript_budget,
                    reference_cache,
                )
            version = package_versions[package_path]
            if version != primary_version:
                raise RuntimeError("Invalid EPUB: package documents use different EPUB versions")


def run_epubcheck(path: Path, warnings: list[ConversionWarning]) -> dict[str, object]:
    executable = shutil.which("epubcheck")
    if not executable:
        warnings.append(
            ConversionWarning(
                "EPUBCHECK_UNAVAILABLE", "EPUBCheck is not installed; only internal validation ran."
            )
        )
        return {"available": False, "passed": None}
    result = subprocess.run([executable, str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"EPUBCheck failed:\n{result.stdout}\n{result.stderr}")
    return {"available": True, "passed": True, "output": result.stdout.strip()}


def make_preview(epub: Path, preview_dir: Path, warnings: list[ConversionWarning]) -> Path | None:
    preview_dir.mkdir(parents=True, exist_ok=True)
    target = preview_dir / "index.html"
    try:
        _run(
            [
                "pandoc",
                str(epub),
                "--to=html5",
                "--standalone",
                "--extract-media",
                str(preview_dir / "assets"),
                "--output",
                str(target),
            ]
        )
    except RuntimeError as exc:
        warnings.append(ConversionWarning("PREVIEW_FAILED", str(exc)))
        return None
    value = target.read_text(encoding="utf-8")
    value = value.replace("</head>", f"<style>{_CSS}</style></head>")
    target.write_text(value, encoding="utf-8")
    return target


def write_reports(
    output_dir: Path, result: dict[str, object], manifest: dict[str, object], enabled: bool
) -> Path | None:
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not enabled:
        return None
    json_path = output_dir / "report.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    warnings = cast(list[dict[str, object]], result["warnings"])
    warning_rows = (
        "".join(
            f"<tr><td>{html.escape(str(item['severity']))}</td><td>{html.escape(str(item['code']))}</td>"
            f"<td>{html.escape(str(item['message']))}</td></tr>"
            for item in warnings
        )
        or '<tr><td colspan="3">No warnings</td></tr>'
    )
    quality = cast(dict[str, object], result.get("quality", {}))
    quality_rows = (
        "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in quality.items()
        )
        or '<tr><td colspan="2">No source quality metrics</td></tr>'
    )
    html_path = output_dir / "report.html"
    html_path.write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>Any2Book report</title>'
        f"<style>{_CSS}td,th{{text-align:left}}</style></head><body><h1>Conversion report</h1>"
        f"<p><strong>Status:</strong> {html.escape(str(result['status']))}</p>"
        f"<p><strong>Adapter:</strong> {html.escape(str(result['adapter']))}</p>"
        f"<p><strong>Output:</strong> {html.escape(str(result.get('output', '')))}</p>"
        f"<h2>Quality metrics</h2><table><tbody>{quality_rows}</tbody></table>"
        f"<h2>Warnings</h2><table><thead><tr><th>Severity</th><th>Code</th><th>Message</th>"
        f"</tr></thead><tbody>{warning_rows}</tbody></table></body></html>",
        encoding="utf-8",
    )
    return html_path
