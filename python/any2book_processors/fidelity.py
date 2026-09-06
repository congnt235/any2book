"""Text and asset conservation gates for reflowed PDF content."""

import hashlib
import posixpath
import re
import tempfile
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET


class ContentText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "div", "section", "br", "td", "th", "caption", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.handle_starttag(tag, [])


def text_content(markup: str) -> str:
    parser = ContentText()
    parser.feed(markup)
    return normalize_text("".join(parser.parts))


def normalize_text(text: str) -> str:
    # Only Unicode canonical equivalence and reflow whitespace are allowed.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def remove_navigation_headings(path: Path) -> None:
    """Remove only our generated navigation labels from body content, retaining anchors."""
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        candidate = Path(directory) / "book.epub"
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(candidate, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename.endswith(".xhtml"):
                    data = re.sub(
                        rb'<h1\b[^>]*class="[^"]*\ba2b-navigation-only\b[^"]*"[^>]*>.*?</h1>',
                        b"", data, flags=re.DOTALL,
                    )
                target.writestr(item, data)
        candidate.replace(path)


def verify_epub(path: Path, expected: str, asset_hashes: list[str]) -> None:
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if e.tag.endswith("}rootfile"))
        package_path = rootfile.attrib["full-path"]
        package = ET.fromstring(archive.read(package_path))
        items = {e.attrib["id"]: e for e in package.iter() if e.tag.endswith("}item")}
        texts: list[str] = []
        images: list[str] = []
        for ref in package.iter():
            if not ref.tag.endswith("}itemref"):
                continue
            item = items[ref.attrib["idref"]]
            if "nav" in item.attrib.get("properties", "").split():
                continue
            name = posixpath.normpath(posixpath.join(
                posixpath.dirname(package_path), unquote(item.attrib["href"])
            ))
            root = ET.fromstring(archive.read(name))
            body = next(e for e in root.iter() if e.tag.endswith("}body"))
            def walk(element: Element) -> str:
                tag = element.tag.rsplit("}", 1)[-1]
                boundary = " " if tag in {"p", "div", "section", "br", "td", "th",
                                          "caption", "tr", "h1", "h2"} else ""
                return boundary + (element.text or "") + "".join(
                    walk(child) + (child.tail or "") for child in element
                ) + boundary
            texts.append(normalize_text(walk(body)))
            for image in body.iter():
                if image.tag.endswith("}img"):
                    target = posixpath.normpath(posixpath.join(
                        posixpath.dirname(name), unquote(image.attrib["src"])
                    ))
                    images.append(hashlib.sha256(archive.read(target)).hexdigest())
        if normalize_text(" ".join(texts)) != normalize_text(expected):
            raise RuntimeError("Source preservation failed: EPUB text differs from transcription")
        if images != asset_hashes:
            raise RuntimeError("Source preservation failed: EPUB image occurrences differ")
