# tests/test_font_scale_css.py
import re

CSS_PATH = "frontend/static/lcars.css"

def _read():
    with open(CSS_PATH) as f:
        return f.read()

def test_font_scale_var_in_root():
    """--font-scale must be declared in :root."""
    css = _read()
    root_match = re.search(r':root\s*\{([^}]+)\}', css)
    assert root_match, ":root block not found"
    assert '--font-scale' in root_match.group(1), "--font-scale not in :root"

def test_font_scale_default_is_one():
    """Default value must be 1 (no scaling)."""
    css = _read()
    assert re.search(r'--font-scale\s*:\s*1\b', css), "--font-scale default should be 1"

def test_no_bare_font_size_px():
    """All font-size: Xpx rules must use calc(). None should remain as bare px values."""
    css = _read()
    css_no_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    bare = re.findall(r'font-size\s*:\s*\d+px', css_no_comments)
    assert bare == [], f"Found bare font-size px rules (should use calc): {bare}"

def test_all_font_size_use_font_scale():
    """All font-size rules must reference --font-scale."""
    css = _read()
    css_no_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    rules = re.findall(r'font-size\s*:[^;]+;', css_no_comments)
    bad = [r for r in rules if '--font-scale' not in r]
    assert bad == [], f"font-size rules not using --font-scale: {bad}"
