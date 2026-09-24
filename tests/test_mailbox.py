from datetime import timedelta
from email.message import EmailMessage

from mailminder.mailbox import html_to_text, parse_message


def build(subject="Test", plain=None, html=None, date="Thu, 24 Sep 2026 10:15:00 +0800"):
    msg = EmailMessage()
    msg["From"] = "United Airlines <notifications@united.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = "<abc123@example.com>"
    if plain is not None:
        msg.set_content(plain)
    if html is not None:
        if plain is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    return msg


def test_headers_are_decoded_and_date_keeps_the_sender_offset():
    msg = parse_message(build(subject="您的航班 UA857 已确认", plain="Hello").as_bytes(), "INBOX", 7)
    assert msg.subject == "您的航班 UA857 已确认"
    assert msg.message_id == "abc123@example.com" and msg.key == "abc123@example.com"
    assert msg.date.utcoffset() == timedelta(hours=8)
    assert msg.uid == 7 and msg.mailbox == "INBOX"


def test_plain_part_is_preferred_over_html():
    msg = parse_message(build(plain="Meeting on Friday at 3pm, room 301.",
                              html="<p>HTML version</p>").as_bytes(), "INBOX", 1)
    assert msg.text == "Meeting on Friday at 3pm, room 301."


def test_stub_plain_part_falls_back_to_html():
    html = "<table><tr><td>Flight</td><td>UA857</td></tr><tr><td>Departs</td><td>SFO 11:25 AM</td></tr></table>" * 5
    msg = parse_message(build(plain="View this email in an HTML client.", html=html).as_bytes(), "INBOX", 1)
    assert "UA857" in msg.text and "SFO 11:25 AM" in msg.text


def test_html_to_text_drops_scripts_and_invisible_preheaders():
    html = ("<html><head><title>x</title><style>td{color:red}</style></head><body>"
            "<div style='display:none'>&zwnj;&nbsp;&zwnj;&nbsp;</div>"
            "<script>var a=1;</script><p>Check-in opens&nbsp;24 hours before.</p>"
            "<table><tr><td>Gate</td><td>G12</td></tr></table></body></html>")
    text = html_to_text(html)
    assert "var a" not in text and "td{" not in text and "\u200c" not in text
    assert "Check-in opens 24 hours before." in text
    assert "Gate G12" in text


def test_gb2312_label_with_gbk_only_characters_decodes():
    body = "陶喆 明天上午十点开会".encode("gbk")  # 喆 is outside GB2312
    raw = (b"From: a@b.cn\r\nSubject: =?gb2312?B?" + __import__("base64").b64encode("通知".encode("gbk"))
           + b"?=\r\nDate: Thu, 24 Sep 2026 10:15:00 +0800\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: text/plain; charset=gb2312\r\nContent-Transfer-Encoding: 8bit\r\n\r\n" + body)
    msg = parse_message(raw, "INBOX", 1)
    assert msg.subject == "通知"
    assert msg.text == "陶喆 明天上午十点开会"


def test_calendar_invite_is_passed_through():
    msg = build(plain="You are invited.")
    ics = ("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
           "DTSTART;TZID=America/New_York:20261005T140000\r\nSUMMARY:Design review\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    msg.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics")
    parsed = parse_message(msg.as_bytes(), "INBOX", 1)
    assert "You are invited." in parsed.text
    assert "DTSTART;TZID=America/New_York:20261005T140000" in parsed.text


def test_other_attachments_are_skipped_and_missing_date_is_none():
    msg = build(plain="See attached.")
    del msg["Date"]
    msg.add_attachment(b"%PDF-1.4 binary", maintype="application", subtype="pdf", filename="a.pdf")
    parsed = parse_message(msg.as_bytes(), "INBOX", 1)
    assert parsed.text == "See attached."
    assert parsed.date is None


def test_missing_message_id_uses_a_content_hash():
    msg = build(plain="x")
    del msg["Message-ID"]
    parsed = parse_message(msg.as_bytes(), "INBOX", 1)
    assert parsed.key.startswith("sha256:")
