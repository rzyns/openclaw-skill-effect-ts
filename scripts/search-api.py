#!/usr/bin/env python3
"""
search-api.py — Search the Effect-TS FTS5 index.

Usage:
  python3 scripts/search-api.py <query> [options]

Examples:
  python3 scripts/search-api.py "Effect.retry Schedule.exponential"
  python3 scripts/search-api.py "tagged error Data.TaggedError" --limit 5
  python3 scripts/search-api.py "sql transaction" --source api-reference
  python3 scripts/search-api.py "Layer.provide" --module effect/Layer

Options:
  --limit N        Max results (default: 8)
  --source NAME    Filter by source: llms-full | api-reference
  --module NAME    Filter by module (partial match), e.g. "effect/Schedule"
  --db PATH        Path to index DB (default: scripts/effect-api.db)
  --json           Output as JSON (for programmatic use)
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
DEFAULT_DB  = SCRIPTS_DIR / "effect-api.db"


def search(db_path: Path, query: str, limit: int, source: str | None,
           module: str | None, as_json: bool):

    if not db_path.exists():
        print(f"ERROR: index not found at {db_path}", file=sys.stderr)
        print("Run:  python3 scripts/build-index.py", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Build WHERE clause
    conditions = ["chunks MATCH ?"]
    params: list = [query]

    if source:
        conditions.append("c.source = ?")
        params.append(source)

    if module:
        conditions.append("c.module LIKE ?")
        params.append(f"%{module}%")

    where = " AND ".join(conditions)

    sql = f"""
        SELECT
            c.source,
            c.module,
            c.section,
            c.content,
            m.url,
            c.rank
        FROM chunks c
        JOIN chunks_meta m ON m.rowid = c.rowid
        WHERE {where}
        ORDER BY c.rank
        LIMIT ?
    """
    params.append(limit)

    # FTS5 treats '.' as a token separator — quote dotted names automatically
    # e.g. "Effect.retry" -> '"Effect" "retry"' so both tokens are searched
    fts_query = params[0]
    if "." in fts_query and not fts_query.startswith('"'):
        fts_query = " ".join(
            f'"{tok}"' if "." in tok else tok
            for tok in fts_query.split()
        )
        params[0] = fts_query

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    conn.close()

    if not rows:
        if not as_json:
            print("No results.")
        else:
            print("[]")
        return

    if as_json:
        out = []
        for row in rows:
            out.append({
                "source":  row["source"],
                "module":  row["module"],
                "section": row["section"],
                "url":     row["url"],
                "content": row["content"][:800],
            })
        print(json.dumps(out, indent=2))
        return

    # Human-readable output
    print(f"\n{'─' * 72}")
    for i, row in enumerate(rows, 1):
        print(f"[{i}] {row['module']}  ({row['source']})")
        print(f"    URL: {row['url']}")
        print(f"    Section: {row['section']}")
        print()
        # Print first 600 chars of content, truncated cleanly at a newline
        content = row["content"]
        if len(content) > 600:
            content = content[:600].rsplit("\n", 1)[0] + "\n    …"
        for line in content.splitlines():
            print(f"    {line}")
        print(f"{'─' * 72}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query",            help="FTS5 search query")
    parser.add_argument("--limit",  type=int, default=8)
    parser.add_argument("--source", choices=["llms-full", "api-reference"])
    parser.add_argument("--module")
    parser.add_argument("--db",     type=Path, default=DEFAULT_DB)
    parser.add_argument("--json",   action="store_true")
    args = parser.parse_args()

    search(args.db, args.query, args.limit, args.source, args.module, args.json)
