# tests/test_font_scale_ui.py
"""Parser-level checks on theme.html for the font scale stepper."""
from pathlib import Path

HTML = Path("frontend/theme.html").read_text(encoding="utf-8")

def test_font_scale_stepper_html_present():
    """Stepper buttons and display element must exist."""
    assert 'id="th-scale-val"' in HTML, "Missing scale value display element"
    assert 'onFontScaleChange(-1)' in HTML, "Missing decrement button call"
    assert 'onFontScaleChange(1)' in HTML, "Missing increment button call"

def test_font_scale_js_function_present():
    """onFontScaleChange() JS function must be defined."""
    assert 'function onFontScaleChange(' in HTML, "Missing onFontScaleChange function"

def test_font_scale_uses_overrides():
    """JS function must save --font-scale into _overrides."""
    assert "_overrides['--font-scale']" in HTML, "Missing _overrides assignment for --font-scale"

def test_font_scale_range_clamped():
    """JS function must clamp to 80-140 range, verified within onFontScaleChange."""
    fn_idx = HTML.index('function onFontScaleChange(')
    next_fn_idx = HTML.index('\n    function ', fn_idx + 1)
    fn_body = HTML[fn_idx:next_fn_idx]
    assert '0.8' in fn_body, "Min clamp value 0.8 not found in onFontScaleChange"
    assert '1.4' in fn_body, "Max clamp value 1.4 not found in onFontScaleChange"

def test_sync_inputs_updates_stepper():
    """syncInputsFromCSS must update the scale display."""
    assert 'th-scale-val' in HTML
    sync_idx = HTML.index('function syncInputsFromCSS')
    next_fn_idx = HTML.index('\n    function ', sync_idx + 1)
    sync_body = HTML[sync_idx:next_fn_idx]
    assert 'th-scale-val' in sync_body, "syncInputsFromCSS does not update stepper display"
