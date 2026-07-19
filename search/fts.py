import re
from core import manager
from search.query import detect_mode


def _sanitize_fts_query(query: str) -> str:
    """Strip characters that break the FTS5 query parser.

    Preserves trailing * after a word character for FTS5 prefix matching
    (e.g. 'doc*' matches 'document', 'docker', etc.).
    All other non-word, non-space characters are replaced with a space.
    """
    # Remove * that is NOT immediately preceded by a word char (standalone or leading *)
    # Remove all other non-word, non-space chars
    sanitized = re.sub(r'(?<!\w)\*|[^\w\s*]', ' ', query)
    return sanitized.strip()


def _register_regexp(conn):
    """Register a case-insensitive REGEXP function on a sqlite3 connection."""
    def _regexp(pattern, text):
        if text is None:
            return False
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            return False
    conn.create_function('REGEXP', 2, _regexp)


def search(db_path: str, query: str, limit: int = 20,
           file_type: str = None, date_from: str = None, date_to: str = None,
           vault_ids: list = None) -> list[dict]:
    """Full-text search via SQLite FTS5.

    Query shapes (auto-detected):
      /pattern/  — regex scan on chunk content (slow; full table scan)
      word*      — FTS5 prefix match (fast; native)
      plain text — FTS5 ranked match (fast; native)
    """
    mode, value = detect_mode(query)

    if mode == 'regex':
        return _regex_search(db_path, value, limit, file_type, date_from, date_to, vault_ids)

    safe_query = _sanitize_fts_query(query)
    if not safe_query:
        return []
    return manager.fts_search(db_path, safe_query, limit,
                               file_type=file_type, date_from=date_from, date_to=date_to,
                               vault_ids=vault_ids)


def _regex_search(db_path: str, pattern: str, limit: int,
                  file_type: str, date_from: str, date_to: str,
                  vault_ids: list = None) -> list[dict]:
    """Scan fts_index chunks with a Python regex. Full table scan — use sparingly."""
    try:
        re.compile(pattern)  # validate before hitting DB
    except re.error:
        return []

    from core.manager import _connect
    with _connect(db_path) as conn:
        _register_regexp(conn)
        where = ["fts_index.content REGEXP ?", "tasks.status != 'MISSING'"]
        params = [pattern]
        if file_type:
            where.append("tasks.file_type LIKE ?")
            params.append(f"%{file_type.strip()}%")
        if date_from:
            where.append("tasks.file_modified >= ?")
            params.append(date_from)
        if date_to:
            where.append("tasks.file_modified <= ?")
            params.append(date_to + "T23:59:59")

        if vault_ids:
            # Move vault_id restriction into the JOIN ON clause to prevent duplicate rows
            # when a file belongs to multiple vaults.
            placeholders = ','.join(['?'] * len(vault_ids))
            join_params = list(vault_ids)
            clause = " AND ".join(where)
            rows = conn.execute(
                f"""SELECT fts_index.file_hash, fts_index.chunk_index, fv.file_path,
                           fts_index.content AS chunk_text,
                           NULL AS snippet,
                           0 AS rank
                    FROM fts_index
                    JOIN tasks ON fts_index.file_hash = tasks.file_hash
                    JOIN file_vault fv ON fts_index.file_hash = fv.file_hash AND fv.vault_id IN ({placeholders})
                    WHERE {clause}
                    LIMIT ?""",
                join_params + params + [limit]
            ).fetchall()
        else:
            clause = " AND ".join(where)
            rows = conn.execute(
                f"""SELECT fts_index.file_hash, fts_index.chunk_index, fts_index.file_path,
                           fts_index.content AS chunk_text,
                           NULL AS snippet,
                           0 AS rank
                    FROM fts_index
                    JOIN tasks ON fts_index.file_hash = tasks.file_hash
                    WHERE {clause}
                    LIMIT ?""",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]
