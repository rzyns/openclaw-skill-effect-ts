#!/usr/bin/env python3
"""
build-index.py — Build an FTS5 SQLite index over:
  1. effect llms-full.txt  (narrative docs + examples)
  2. effect-ts.github.io API reference (per-module TypeDoc HTML)

Output: scripts/effect-api.db

Usage:
  python3 scripts/build-index.py [--db path/to/effect-api.db] [--force]

Options:
  --db PATH    Path to output SQLite database (default: scripts/effect-api.db)
  --force      Re-download all sources even if cached
  --skip-api   Skip API reference crawl (faster, docs only)

Requires: Python 3.9+, no third-party deps (urllib + html.parser + sqlite3)
"""

import argparse
import html.parser
import io
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SKILL_DIR   = Path(__file__).parent.parent
SCRIPTS_DIR = Path(__file__).parent
CACHE_DIR   = SCRIPTS_DIR / ".cache"
DEFAULT_DB  = SCRIPTS_DIR / "effect-api.db"

LLMS_FULL_URL = "https://effect.website/llms-full.txt"
API_BASE_URL  = "https://effect-ts.github.io/effect"
API_INDEX_URL = f"{API_BASE_URL}/docs/effect"

# Packages to index from the API reference.
# Extend this list if you add @effect/platform, @effect/rpc, etc.
API_PACKAGES = [
    "effect",          # core: Effect, Stream, Fiber, Schedule, Layer, Schema, ...
    "sql",             # @effect/sql
    "sql-bun",         # @effect/sql-bun
    "ai",              # @effect/ai
    "ai-anthropic",    # @effect/ai-anthropic
    "ai-openai",       # @effect/ai-openai
]

RATE_LIMIT_S = 0.15   # seconds between HTTP requests (be polite)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(url: str, cache_path: Path, force: bool = False) -> bytes:
    """Fetch URL, cache to disk, return bytes."""
    if not force and cache_path.exists():
        return cache_path.read_bytes()
    print(f"  GET {url}", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "effect-skill-indexer/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return b""
        raise
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    time.sleep(RATE_LIMIT_S)
    return data


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class TextExtractor(html.parser.HTMLParser):
    """Minimal HTML → plain text extractor."""

    SKIP_TAGS = {"script", "style", "nav", "header", "footer"}

    def __init__(self):
        super().__init__()
        self._skip  = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def text(self) -> str:
        return "\n".join(self._parts)


def html_to_text(raw: bytes) -> str:
    parser = TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser.text()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_llms_full(text: str) -> Iterator[dict]:
    """
    Split llms-full.txt into sections by markdown headings.
    Yields dicts: {source, module, section, content}
    """
    current_h1 = ""
    current_h2 = ""
    buf = []

    def flush():
        content = "\n".join(buf).strip()
        if content:
            yield {
                "source":  "llms-full",
                "module":  current_h1,
                "section": current_h2 or current_h1,
                "content": content,
            }
        buf.clear()

    for line in text.splitlines():
        if line.startswith("# "):
            yield from flush()
            current_h1 = line.lstrip("# ").strip()
            current_h2 = ""
        elif line.startswith("## "):
            yield from flush()
            current_h2 = line.lstrip("# ").strip()
        else:
            buf.append(line)

    yield from flush()


def chunk_api_page(package: str, module: str, text: str) -> Iterator[dict]:
    """
    Split an API reference page into per-function chunks.
    Heuristic: each exported function/type starts with its name on a line,
    followed by its signature.  We chunk on blank lines between entries.
    """
    # Split on double-newline boundaries; keep groups of ≤60 lines together
    paragraphs = re.split(r"\n{2,}", text)
    buf = []
    buf_lines = 0

    for para in paragraphs:
        lines = para.count("\n") + 1
        if buf_lines + lines > 80 and buf:
            yield {
                "source":  "api-reference",
                "module":  f"{package}/{module}",
                "section": module,
                "content": "\n\n".join(buf).strip(),
            }
            buf = []
            buf_lines = 0
        buf.append(para)
        buf_lines += lines

    if buf:
        content = "\n\n".join(buf).strip()
        if content:
            yield {
                "source":  "api-reference",
                "module":  f"{package}/{module}",
                "section": module,
                "content": content,
            }


# ---------------------------------------------------------------------------
# API reference module list
# ---------------------------------------------------------------------------

class ModuleListParser(html.parser.HTMLParser):
    """Extract .html links for a given package from the sidebar nav."""

    def __init__(self, package: str):
        super().__init__()
        self._package = package
        self.modules: list[str] = []
        self._in_section = False

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        # Links look like /effect/sql/SqlClient.ts.html
        pattern = f"/effect/{self._package}/"
        if href.startswith(pattern) and href.endswith(".html"):
            module = href.split("/")[-1].replace(".html", "")
            if module not in ("index", "index.ts"):
                self.modules.append(module)

    def unique_modules(self) -> list[str]:
        seen = set()
        out = []
        for m in self.modules:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out


def get_module_list(package: str, force: bool) -> list[str]:
    cache = CACHE_DIR / f"index-{package}.html"
    url   = f"{API_BASE_URL}/docs/{package}"
    raw   = fetch(url, cache, force)
    if not raw:
        # Try the effect package index differently
        url2  = f"{API_BASE_URL}/docs/effect"
        raw   = fetch(url2, CACHE_DIR / "index-effect.html", force)
    parser = ModuleListParser(package)
    parser.feed(raw.decode("utf-8", errors="replace"))
    modules = parser.unique_modules()
    print(f"  [{package}] {len(modules)} modules found")
    return modules


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build(db_path: Path, force: bool, skip_api: bool):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE VIRTUAL TABLE chunks USING fts5(
            source,
            module,
            section,
            content,
            tokenize = 'porter unicode61'
        )
    """)
    conn.execute("""
        CREATE TABLE chunks_meta (
            rowid   INTEGER PRIMARY KEY,
            source  TEXT,
            module  TEXT,
            section TEXT,
            url     TEXT
        )
    """)
    conn.commit()

    total = 0

    # ── 1. llms-full.txt ────────────────────────────────────────────────────
    print("\n[1/2] Fetching llms-full.txt …")
    cache_llms = CACHE_DIR / "llms-full.txt"
    raw_llms   = fetch(LLMS_FULL_URL, cache_llms, force)
    text_llms  = raw_llms.decode("utf-8", errors="replace")

    rows_llms = list(chunk_llms_full(text_llms))
    print(f"  {len(rows_llms)} chunks from narrative docs")

    for chunk in rows_llms:
        conn.execute(
            "INSERT INTO chunks(source, module, section, content) VALUES (?,?,?,?)",
            (chunk["source"], chunk["module"], chunk["section"], chunk["content"]),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO chunks_meta(rowid, source, module, section, url) VALUES (?,?,?,?,?)",
            (rowid, chunk["source"], chunk["module"], chunk["section"], LLMS_FULL_URL),
        )
        total += 1

    conn.commit()

    # ── 2. API reference ────────────────────────────────────────────────────
    if not skip_api:
        print("\n[2/2] Crawling API reference …")
        for package in API_PACKAGES:
            print(f"\n  Package: {package}")
            modules = get_module_list(package, force)
            for module_file in modules:
                url       = f"{API_BASE_URL}/{package}/{module_file}"
                cache_key = CACHE_DIR / "api" / package / f"{module_file}"
                raw       = fetch(url, cache_key, force)
                if not raw:
                    continue
                text = html_to_text(raw)
                # Strip the module name from the file (e.g. "Effect.ts" -> "Effect")
                module_name = module_file.replace(".ts.html", "").replace(".html", "")
                chunks = list(chunk_api_page(package, module_name, text))
                for chunk in chunks:
                    conn.execute(
                        "INSERT INTO chunks(source, module, section, content) VALUES (?,?,?,?)",
                        (chunk["source"], chunk["module"], chunk["section"], chunk["content"]),
                    )
                    rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute(
                        "INSERT INTO chunks_meta(rowid, source, module, section, url) VALUES (?,?,?,?,?)",
                        (rowid, chunk["source"], chunk["module"], chunk["section"], url),
                    )
                    total += 1
                conn.commit()

    conn.close()
    print(f"\n✓ Index built: {db_path}  ({total} chunks total)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",       type=Path, default=DEFAULT_DB)
    parser.add_argument("--force",    action="store_true")
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args()
    build(args.db, args.force, args.skip_api)
