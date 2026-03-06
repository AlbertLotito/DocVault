from core.router import get_extractors, UNKNOWN


def test_pdf_routes_to_text_and_image():
    extractors = get_extractors('pdf')
    names = [e.__name__ for e in extractors]
    assert 'text_extractor' in names
    assert 'image_extractor' in names


def test_docx_routes_to_word():
    extractors = get_extractors('docx')
    names = [e.__name__ for e in extractors]
    assert 'microsoft_word_extractor' in names


def test_txt_routes_to_plaintext():
    extractors = get_extractors('txt')
    names = [e.__name__ for e in extractors]
    assert 'plaintext_extractor' in names


def test_wav_routes_to_metadata_and_aural_ai():
    extractors = get_extractors('wav')
    names = [e.__name__ for e in extractors]
    assert 'media_technical_diagnostics_extractor' in names
    assert 'aural_intelligence_extractor' in names


def test_mp4_routes_to_multimodal_video():
    extractors = get_extractors('mp4')
    names = [e.__name__ for e in extractors]
    assert 'multimodal_video_intelligence_extractor' in names


def test_unknown_extension_returns_unknown():
    extractors = get_extractors('xyz123')
    assert extractors == [UNKNOWN]


def test_jpg_routes_to_intelligent_image():
    extractors = get_extractors('jpg')
    names = [e.__name__ for e in extractors]
    assert 'intelligent_image_extractor' in names
