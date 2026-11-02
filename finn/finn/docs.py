"""Turn a filing PDF into per-page text. Page numbers are kept so every claim can cite one."""
from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass, field

import pymupdf

from .net import fetch_file, fetch_linked_pdf
from .store import db

pymupdf.TOOLS.mupdf_display_errors(False)


@dataclass
class Doc:
    url: str
    pages: list[str]
    links: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self.pages)

    @property
    def scanned(self) -> bool:
        """Image-only PDFs yield almost no text; report that rather than pretend."""
        return bool(self.pages) and self.chars / len(self.pages) < 80


class NotAPdf(ValueError):
    """Some document links return an HTML page with a 200. PyMuPDF would happily open that."""


def keep(doc: Doc) -> None:
    """Store a document's text in the database, compressed. A transcript PDF of several MB becomes about
    20 KB and loses nothing FINN uses, so a fresh deploy can still answer questions with no source reachable."""
    db().execute("INSERT OR REPLACE INTO doc_text VALUES (?, ?)", (doc.url, zlib.compress(json.dumps(doc.pages, ensure_ascii=False).encode(), 9)))
    db().commit()


def load(url: str, *, headers: dict | None = None, max_pages: int | None = None) -> Doc:
    row = db().execute("SELECT pages FROM doc_text WHERE url=?", (url,)).fetchone()
    if row:
        pages = json.loads(zlib.decompress(row["pages"]))
        return Doc(url=url, pages=pages[:max_pages] if max_pages else pages)
    return _read(fetch_file(url, headers=headers), url, max_pages)


def load_linked(url: str) -> Doc:
    """A link taken from inside another filing. See net.fetch_linked_pdf for why this is separate."""
    return _read(fetch_linked_pdf(url), url, None)


def _read(path, url: str, max_pages: int | None) -> Doc:
    with open(path, "rb") as f:
        if not f.read(1024).lstrip().startswith(b"%PDF"):
            raise NotAPdf(url)
    with pymupdf.open(path, filetype="pdf") as pdf:
        n = len(pdf) if max_pages is None else min(max_pages, len(pdf))
        pages, links = [], []
        for i in range(n):
            pages.append(_clean(pdf[i].get_text("text", sort=True)))
            links += [l["uri"] for l in pdf[i].get_links() if l.get("uri")]
            # MuPDF's cache of fonts and images grows page by page, which matters on a 512 MB server.
            if i % 20 == 19:
                pymupdf.TOOLS.store_shrink(100)
    pymupdf.TOOLS.store_shrink(100)
    return Doc(url=url, pages=pages, links=links)


def _clean(t: str) -> str:
    t = t.replace(" ", " ").replace("\r", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
