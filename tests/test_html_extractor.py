import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock

from extractors import html_extractor


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='test', file_hash='test',
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


PAGE = """<!DOCTYPE html>
<html><head>
  <title>Chapter Four</title>
  <meta name="description" content="Step five of the basic text">
  <style>body { color: red; }</style>
  <script>var tracking = "SECRET_JS";</script>
</head>
<body>
  <!-- a comment that should vanish -->
  <h1>Step Five</h1>
  <p>Admitted to God, to ourselves,<br>and to another human being.</p>
  <div><span class="sd-abs-pos" style="position:absolute">Inline</span> <b>bold</b> text.</div>
  <ul><li>First</li><li>Second</li></ul>
  <noscript>Enable JS</noscript>
</body></html>"""


def _write(tmp_path, name, content, encoding='utf-8'):
    f = tmp_path / name
    f.write_bytes(content.encode(encoding))
    return str(f)


def test_extracts_visible_text_without_markup(tmp_path):
    text, error, meta = html_extractor.extract(_write(tmp_path, 'page.html', PAGE), _ctx())

    assert error is None
    for gone in ('<', '>', 'sd-abs-pos', 'color: red', 'SECRET_JS', 'a comment', 'Enable JS', 'DOCTYPE'):
        assert gone not in text
    for kept in ('Step Five', 'Admitted to God', 'Inline bold text.', 'First', 'Second'):
        assert kept in text


def test_keeps_block_and_line_structure(tmp_path):
    text, _, _ = html_extractor.extract(_write(tmp_path, 'page.html', PAGE), _ctx())
    lines = [l for l in text.split('\n') if l.strip()]
    assert 'Step Five' in lines
    assert 'Admitted to God, to ourselves,' in lines          # <br> breaks the line
    assert 'and to another human being.' in lines
    assert 'First' in lines and 'Second' in lines            # each <li> on its own line
    assert '\n\n\n' not in text


def test_title_and_description_become_metadata(tmp_path):
    _, _, meta = html_extractor.extract(_write(tmp_path, 'page.html', PAGE), _ctx())
    assert meta['title'] == 'Chapter Four'
    assert meta['description'] == 'Step five of the basic text'


def test_honours_declared_legacy_encoding(tmp_path):
    page = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'
            '</head><body><p>Café crème à la française</p></body></html>')
    text, error, _ = html_extractor.extract(_write(tmp_path, 'old.htm', page, 'iso-8859-1'), _ctx())
    assert error is None
    assert 'Café crème à la française' in text


def test_decodes_entities(tmp_path):
    page = '<html><body><p>Fish &amp; chips &mdash; &pound;5 &#169;</p></body></html>'
    text, _, _ = html_extractor.extract(_write(tmp_path, 'e.html', page), _ctx())
    assert 'Fish & chips — £5 ©' in text


def test_netscape_bookmark_file_yields_link_titles(tmp_path):
    page = ('<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n'
            '<DL><p>\n<DT><A HREF="https://example.org" ADD_DATE="1">Example Site</A>\n'
            '<DT><A HREF="https://python.org">Python Home</A>\n</DL><p>')
    text, error, _ = html_extractor.extract(_write(tmp_path, 'bookmarks.html', page), _ctx())
    assert error is None
    assert 'Example Site' in text and 'Python Home' in text
    assert 'ADD_DATE' not in text


def test_extracts_html_part_of_mht_archive(tmp_path):
    msg = MIMEMultipart('related')
    msg['Subject'] = 'Saved page'
    msg.attach(MIMEText('<html><head><title>Saved</title></head><body><p>Archived article body</p>'
                        '<script>junk()</script></body></html>', 'html', 'utf-8'))
    f = tmp_path / 'page.mht'
    f.write_bytes(msg.as_bytes())

    text, error, meta = html_extractor.extract(str(f), _ctx())

    assert error is None
    assert 'Archived article body' in text and 'junk' not in text
    assert meta['title'] == 'Saved'


def test_mht_without_html_part_is_an_error(tmp_path):
    msg = MIMEMultipart('related')
    msg.attach(MIMEText('just text', 'plain'))
    f = tmp_path / 'odd.mht'
    f.write_bytes(msg.as_bytes())

    result = html_extractor.extract(str(f), _ctx())

    assert result[0] is None
    assert 'No HTML part' in result[1]


def test_page_with_no_visible_text_is_an_error(tmp_path):
    page = '<html><head><script>x()</script></head><body><img src="a.png"></body></html>'
    result = html_extractor.extract(_write(tmp_path, 'blank.html', page), _ctx())
    assert result[0] is None
    assert 'No visible text' in result[1]


def test_missing_file_is_an_error(tmp_path):
    result = html_extractor.extract(str(tmp_path / 'nope.html'), _ctx())
    assert result[0] is None
    assert result[1]


def test_html_is_routed_only_to_the_html_extractor():
    """Every active kernel for an extension runs as a chain and their text is
    concatenated, so no other kernel may also claim HTML types."""
    import ast
    import glob
    import os
    ext_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'extractors')
    claims = {}
    for path in glob.glob(os.path.join(ext_dir, '*.py')):
        tree = ast.parse(open(path, encoding='utf-8').read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'MANIFEST' for t in node.targets):
                manifest = ast.literal_eval(node.value)
                for ext in manifest.get('extensions', []):
                    claims.setdefault(ext, []).append(os.path.basename(path))
    for ext in html_extractor.MANIFEST['extensions']:
        assert claims[ext] == ['html_extractor.py'], f".{ext} is also claimed by {claims[ext]}"


def test_mime_message_saved_with_htm_extension_is_unpacked(tmp_path):
    """Old mail clients saved messages as .htm files that are really MIME (often quoted-printable)."""
    msg = MIMEMultipart('alternative')
    msg['From'] = 'someone@example.org'
    msg['Subject'] = 'Old mail'
    msg.attach(MIMEText('<html><body><p>Café meeting moved to Tuesday</p></body></html>', 'html', 'iso-8859-1'))
    f = tmp_path / '1996-09-12.htm'
    f.write_bytes(msg.as_bytes())

    text, error, meta = html_extractor.extract(str(f), _ctx())

    assert error is None
    assert 'Café meeting moved to Tuesday' in text
    assert 'Content-Type' not in text and 'MIME' not in text


def test_page_with_only_a_title_indexes_the_title(tmp_path):
    """Frame/script pages often have no body text but a meaningful title."""
    page = ('<html><head><title>George Lotito 1938-2002</title>'
            '<meta name="description" content="Memorial page"></head>'
            '<frameset><frame src="a.htm"></frameset></html>')
    text, error, meta = html_extractor.extract(_write(tmp_path, 'memorial.htm', page), _ctx())
    assert error is None
    assert text == 'George Lotito 1938-2002\nMemorial page'
    assert meta['title'] == 'George Lotito 1938-2002'
