import threading
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock

from extractors import email_extractor


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(vault_id='test', file_hash='test', cancel_token=threading.Event(),
                            logger=MagicMock(spec=ExtractorLogger), settings=MagicMock())


def _headers(msg, subject='Radios'):
    msg['From'] = '"Chuck Margretta" <chuck@example.com>'
    msg['To'] = 'albert@example.net'
    msg['Cc'] = 'ops@example.net'
    msg['Subject'] = subject
    msg['Date'] = 'Mon, 07 Oct 2013 17:43:00 GMT'
    msg['Message-ID'] = '<abc123@example.com>'
    return msg


def _write(tmp_path, msg, name='m.wdseml'):
    f = tmp_path / name
    f.write_bytes(msg.as_bytes() if hasattr(msg, 'as_bytes') else msg)
    return str(f)


def test_plain_text_email_header_block_and_body(tmp_path):
    msg = _headers(MIMEText('Al,\n\nPer our conversation I have the following radios.\n', 'plain', 'utf-8'))

    text, error, meta = email_extractor.extract(_write(tmp_path, msg), _ctx())

    assert error is None
    head, body = text.split('\n\n', 1)
    assert head.splitlines() == [
        'From: Chuck Margretta <chuck@example.com>',
        'To: albert@example.net',
        'Cc: ops@example.net',
        'Subject: Radios',
        'Date: Mon, 07 Oct 2013 17:43:00 +0000',   # parser normalises GMT
    ]
    assert 'Per our conversation I have the following radios.' in body


def test_metadata(tmp_path):
    msg = _headers(MIMEText('hello', 'plain'))
    _, _, meta = email_extractor.extract(_write(tmp_path, msg), _ctx())
    assert meta['from'] == 'Chuck Margretta <chuck@example.com>'
    assert meta['to'] == 'albert@example.net'
    assert meta['cc'] == 'ops@example.net'
    assert meta['subject'] == 'Radios'
    assert meta['date'] == '2013-10-07T17:43:00+00:00'
    assert meta['message_id'] == '<abc123@example.com>'
    assert 'attachments' not in meta


def test_html_only_email_is_rendered_to_text(tmp_path):
    html = '<html><body><p>Meeting <b>moved</b> to Tuesday</p><script>track()</script></body></html>'
    msg = _headers(MIMEText(html, 'html', 'utf-8'))
    text, error, _ = email_extractor.extract(_write(tmp_path, msg), _ctx())
    assert error is None
    assert 'Meeting moved to Tuesday' in text
    assert '<' not in text.split('\n\n', 1)[1] and 'track()' not in text


def test_prefers_plain_part_of_alternative(tmp_path):
    msg = _headers(MIMEMultipart('alternative'))
    msg.attach(MIMEText('PLAIN VERSION', 'plain'))
    msg.attach(MIMEText('<p>HTML VERSION</p>', 'html'))
    text, _, _ = email_extractor.extract(_write(tmp_path, msg), _ctx())
    assert 'PLAIN VERSION' in text and 'HTML VERSION' not in text


def test_attachments_are_listed_not_extracted(tmp_path):
    msg = _headers(MIMEMultipart('mixed'))
    msg.attach(MIMEText('See attached.', 'plain'))
    att = MIMEApplication(b'%PDF-1.4 binary', Name='invoice-june.pdf')
    att['Content-Disposition'] = 'attachment; filename="invoice-june.pdf"'
    msg.attach(att)
    txt_att = MIMEText('SECRET ATTACHMENT TEXT', 'plain')
    txt_att['Content-Disposition'] = 'attachment; filename="notes.txt"'
    msg.attach(txt_att)

    text, error, meta = email_extractor.extract(_write(tmp_path, msg), _ctx())

    assert error is None
    assert 'See attached.' in text
    assert 'Attachments: invoice-june.pdf, notes.txt' in text
    assert 'SECRET ATTACHMENT TEXT' not in text     # a text/plain attachment is not the body
    assert meta['attachments'] == ['invoice-june.pdf', 'notes.txt']


def test_encoded_headers_and_legacy_charset(tmp_path):
    msg = MIMEText('Café crème demain', 'plain', 'iso-8859-1')
    msg['From'] = Header('Renée François', 'utf-8').encode() + ' <renee@example.fr>'
    msg['Subject'] = Header('Réunion à midi', 'utf-8')
    msg['Date'] = 'Tue, 08 Oct 2013 09:00:00 +0200'
    text, error, meta = email_extractor.extract(_write(tmp_path, msg), _ctx())
    assert error is None
    assert meta['subject'] == 'Réunion à midi'
    assert meta['from'] == 'Renée François <renee@example.fr>'
    assert 'Café crème demain' in text
    assert meta['date'] == '2013-10-08T09:00:00+02:00'


def test_header_only_message_still_indexes_headers(tmp_path):
    msg = _headers(MIMEText('', 'plain'), subject='Quick ping')
    text, error, _ = email_extractor.extract(_write(tmp_path, msg), _ctx())
    assert error is None
    assert 'Subject: Quick ping' in text


def test_unparseable_date_is_kept_raw(tmp_path):
    msg = MIMEText('body', 'plain')
    msg['From'] = 'a@example.com'
    msg['Date'] = 'sometime last week'
    _, _, meta = email_extractor.extract(_write(tmp_path, msg), _ctx())
    assert meta['date'] == 'sometime last week'


def test_non_email_file_is_an_error(tmp_path):
    f = tmp_path / 'junk.eml'
    f.write_bytes(b'\x00\x01\x02 just some binary \xff\xfe')
    result = email_extractor.extract(str(f), _ctx())
    assert result[0] is None
    assert 'Not an email message' in result[1]


def test_missing_file_is_an_error(tmp_path):
    result = email_extractor.extract(str(tmp_path / 'nope.eml'), _ctx())
    assert result[0] is None and result[1]
