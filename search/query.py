"""
search/query.py — Query shape detection shared by FTS and filename search.

No imports from core or search to avoid circular dependencies.
"""
import re

_REGEX_DELIMITERS = re.compile(r'^/(.+)/$', re.DOTALL)
_WILDCARD_CHARS = frozenset('*?')


def detect_mode(query: str) -> tuple[str, str]:
    """Detect query intent from its shape. Returns (mode, value).

    Modes
    -----
    'regex'    /pattern/ syntax — value is the inner regex pattern.
    'wildcard' Contains * or ? — value is the raw query.
    'plain'    Normal text — value is the raw query.

    Examples
    --------
    detect_mode('/foo.*bar/')  -> ('regex', 'foo.*bar')
    detect_mode('report*.pdf')  -> ('wildcard', 'report*.pdf')
    detect_mode('annual report') -> ('plain', 'annual report')
    """
    q = query.strip()
    m = _REGEX_DELIMITERS.match(q)
    if m:
        return 'regex', m.group(1)
    if _WILDCARD_CHARS.intersection(q):
        return 'wildcard', q
    return 'plain', q
