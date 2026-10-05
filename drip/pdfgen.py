"""Minimal PDF writer (stdlib only) for test fixtures and demo samples.

Text is Helvetica with a cp1254 /Differences encoding so Turkish letters survive extraction.
`hidden` lines are drawn in white, 1pt: invisible to a reader, visible to text extraction, which is
how indirect prompt injection is usually smuggled into documents.
"""

import textwrap

# cp1254 code points that WinAnsi maps differently.
DIFFERENCES = "[208 /Gbreve 221 /Idotaccent 222 /Scedilla 240 /gbreve 253 /dotlessi 254 /scedilla]"
LINES_PER_PAGE = 48
WIDTH = 95


def _escape(s: str) -> bytes:
    raw = s.encode("cp1254", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _page_stream(lines: list[str], hidden: list[str]) -> bytes:
    out = [b"BT /F1 10 Tf 14 TL 50 800 Td"]
    for line in lines:
        out.append(b"(" + _escape(line) + b") '")
    out.append(b"ET")
    if hidden:
        out.append(b"BT 1 1 1 rg /F1 1 Tf 2 TL 50 30 Td")
        for line in hidden:
            out.append(b"(" + _escape(line) + b") '")
        out.append(b"ET")
    return b"\n".join(out)


def make_pdf(paragraphs: list[str], hidden: list[str] | None = None, title: str = "") -> bytes:
    lines: list[str] = [title, ""] if title else []
    for p in paragraphs:
        lines += textwrap.wrap(p, WIDTH) or [""]
        lines.append("")
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]

    objects: list[bytes] = []
    add = lambda body: objects.append(body) or len(objects)
    catalog = add(b"")          # filled in below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /Type /Encoding "
               b"/BaseEncoding /WinAnsiEncoding /Differences " + DIFFERENCES.encode() + b" >> >>")
    kids = []
    for i, page_lines in enumerate(pages):
        stream = _page_stream(page_lines, (hidden or []) if i == 0 else [])
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                        b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_obj, content, font)))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objects[pages_obj - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                              + b"] /Count %d >>" % len(kids))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    return bytes(out)


def malformed_pdf() -> bytes:
    """Looks like a PDF, isn't one: broken objects, no xref, truncated."""
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R\nstream\n\x00\x01garbage\xff\xfe" + b"\x8f" * 64
