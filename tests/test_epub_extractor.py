import threading
from unittest.mock import MagicMock

from ebooklib import epub

from extractors import epub_extractor


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='test', file_hash='test',
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


def _chapter(name, body):
    ch = epub.EpubHtml(title=name, file_name=f'{name}.xhtml', lang='en')
    ch.content = f'<html><body><h1>{name}</h1><p>{body}</p></body></html>'
    return ch


def _write_book(path, chapters, spine_order=None, title='Test Book', author='A. Writer'):
    """Chapters are added to the manifest in list order; spine_order overrides reading order."""
    book = epub.EpubBook()
    book.set_identifier('id-123')
    book.set_title(title)
    book.set_language('en')
    if author:
        book.add_author(author)
    for ch in chapters:
        book.add_item(ch)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine_order or chapters
    epub.write_epub(str(path), book)


def test_extracts_text_and_metadata(tmp_path):
    f = tmp_path / 'book.epub'
    _write_book(f, [_chapter('One', 'First chapter text.')])

    result = epub_extractor.extract(str(f), _ctx())

    text, error, meta = result
    assert error is None
    assert 'First chapter text.' in text
    assert meta == {'title': 'Test Book', 'author': 'A. Writer'}


def test_chapters_follow_spine_reading_order_not_manifest_order(tmp_path):
    f = tmp_path / 'book.epub'
    intro = _chapter('Intro', 'INTRO_MARKER')
    ending = _chapter('Ending', 'ENDING_MARKER')
    # Manifest lists Ending first; the spine says Intro is read first.
    _write_book(f, [ending, intro], spine_order=[intro, ending])

    text, error, _ = epub_extractor.extract(str(f), _ctx())

    assert error is None
    assert text.index('INTRO_MARKER') < text.index('ENDING_MARKER')


def test_strips_private_use_area_glyphs(tmp_path):
    f = tmp_path / 'book.epub'
    _write_book(f, [_chapter('One', 'Bullet' + chr(0xE001) + ' point')])

    text, error, _ = epub_extractor.extract(str(f), _ctx())

    assert error is None
    assert chr(0xE001) not in text
    assert 'Bullet point' in text


def test_book_without_text_is_an_error(tmp_path):
    f = tmp_path / 'book.epub'
    empty = epub.EpubHtml(title='Cover', file_name='cover.xhtml', lang='en')
    empty.content = '<html><body><img src="cover.jpg"/></body></html>'
    _write_book(f, [empty])

    result = epub_extractor.extract(str(f), _ctx())

    assert result[0] is None
    assert 'No text content' in result[1]


def test_unreadable_file_is_an_error(tmp_path):
    f = tmp_path / 'broken.epub'
    f.write_bytes(b'not a zip at all')

    result = epub_extractor.extract(str(f), _ctx())

    assert result[0] is None
    assert 'Could not open EPUB' in result[1]
