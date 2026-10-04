"""Context menus are position:fixed, so they must be placed with viewport
coordinates. pageX/pageY put the Browse page's menu off-screen once the tree
had been scrolled (found in UI testing 2026-10-04)."""
import os

FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend')
LIVE_PAGES = ('browse.html', 'vault.html')   # catalog.html is the retired page /catalog redirects away from


def test_live_pages_use_the_shared_viewport_placement():
    for page in LIVE_PAGES:
        src = open(os.path.join(FRONTEND, page), encoding='utf-8').read()
        assert 'e.pageX' not in src and 'e.pageY' not in src, page
        assert 'lcOpenCtxMenu(menu, e)' in src, page


def test_helper_uses_client_coordinates():
    src = open(os.path.join(FRONTEND, 'static', 'lcars.js'), encoding='utf-8').read()
    body = src[src.index('function lcOpenCtxMenu'):]
    body = body[:body.index('\n}\n')]
    assert 'e.clientX' in body and 'e.clientY' in body and 'pageX' not in body
