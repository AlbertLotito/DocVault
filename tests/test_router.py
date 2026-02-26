from core.router import get_extractors, UNKNOWN


def test_pdf_routes_to_text_and_image():
    extractors = get_extractors('pdf')
    names = [e.__name__ for e in extractors]
    assert 'text_extractor' in names
    assert 'image_extractor' in names


def test_docx_routes_to_word():
    extractors = get_extractors('docx')
    names = [e.__name__ for e in extractors]
    assert 'word_extractor' in names


def test_txt_routes_to_plaintext():
    extractors = get_extractors('txt')
    names = [e.__name__ for e in extractors]
    assert 'plaintext_extractor' in names


def test_wav_routes_to_metadata_and_transcriber():
    extractors = get_extractors('wav')
    names = [e.__name__ for e in extractors]
    assert 'metadata_extractor' in names
    assert 'transcriber' in names


def test_mp4_routes_to_video():
    extractors = get_extractors('mp4')
    names = [e.__name__ for e in extractors]
    assert 'video_extractor' in names


def test_unknown_extension_returns_unknown():
    extractors = get_extractors('xyz123')
    assert extractors == [UNKNOWN]


def test_jpg_routes_to_ocr():
    extractors = get_extractors('jpg')
    names = [e.__name__ for e in extractors]
    assert 'ocr_extractor' in names
