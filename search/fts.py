import re
from core import manager


def _sanitize_fts_query(query: str) -> str:
    """Strip characters that break the FTS5 query parser, keeping words and spaces."""
    return re.sub(r'[^\w\s]', ' ', query).strip()


def search(db_path: str, query: str, limit: int = 20,
           file_type: str = None, date_from: str = None, date_to: str = None) -> list[dict]:
    """Full-text search via SQLite FTS5. Supports optional date and file-type filters."""
    safe_query = _sanitize_fts_query(query)
    if not safe_query:
        return []
    return manager.fts_search(db_path, safe_query, limit,
                               file_type=file_type, date_from=date_from, date_to=date_to)
