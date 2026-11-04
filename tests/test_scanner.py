import io
import tarfile
import zipfile

from tokencounter import Tokenizer, scan_path

tok = Tokenizer()


def test_plain_and_binary(tmp_path):
    (tmp_path / "a.txt").write_text("hello world")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
    res = {r.path.rsplit("/", 1)[-1]: r for r in scan_path(str(tmp_path), tok)}
    assert res["a.txt"].tokens > 0
    assert res["b.bin"].skipped == "binary"


def test_zip(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("d/one.txt", "hello world")
    res = list(scan_path(str(z), tok))
    assert [r.path.endswith("!/d/one.txt") for r in res] == [True]
    assert res[0].tokens > 0


def test_tarball_compressions_and_nesting(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.txt", "nested content here")
    for mode, ext in [("w", "tar"), ("w:gz", "tar.gz"), ("w:bz2", "tar.bz2"), ("w:xz", "tar.xz")]:
        t = tmp_path / f"x.{ext}"
        with tarfile.open(t, mode) as tf:
            info = tarfile.TarInfo("inner.zip")
            info.size = len(inner.getvalue())
            tf.addfile(info, io.BytesIO(inner.getvalue()))
        res = list(scan_path(str(t), tok))
        assert len(res) == 1 and res[0].path.endswith("inner.zip!/deep.txt") and res[0].tokens > 0, ext


def test_corrupt_archive(tmp_path):
    z = tmp_path / "bad.zip"
    z.write_bytes(b"PK\x03\x04garbage")
    res = list(scan_path(str(z), tok))
    assert res[0].skipped


def _one(path):
    res = list(scan_path(str(path), tok))
    assert len(res) == 1, res
    return res[0]


def test_image(tmp_path):
    from PIL import Image

    Image.new("RGB", (750, 750)).save(tmp_path / "a.png")
    r = _one(tmp_path / "a.png")
    assert (r.kind, r.tokens) == ("image", 750)
    Image.new("RGB", (6000, 6000)).save(tmp_path / "big.png")
    assert _one(tmp_path / "big.png").tokens == 1600  # capped


def test_audio_wav(tmp_path):
    import wave

    with wave.open(str(tmp_path / "s.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * 10)
    r = _one(tmp_path / "s.wav")
    assert (r.kind, r.tokens) == ("audio", 320)


def test_pdf(tmp_path):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(612, 792)
    with open(tmp_path / "a.pdf", "wb") as f:
        w.write(f)
    r = _one(tmp_path / "a.pdf")
    assert r.kind == "pdf" and r.tokens > 0 and "1 pages" in r.note


def test_office_docs(tmp_path):
    p = tmp_path / "a.docx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>Hello &amp; goodbye</w:t></w:r></w:p></w:document>")
    assert _one(p).kind == "document" and _one(p).tokens > 0

    x = tmp_path / "a.xlsx"
    with zipfile.ZipFile(x, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", "<sst><si><t>alpha</t></si></sst>")
        zf.writestr("xl/worksheets/sheet1.xml", '<row><c t="s"><v>0</v></c><c><v>42</v></c></row>')
    assert _one(x).tokens > 0

    (tmp_path / "a.rtf").write_text(r"{\rtf1\ansi Hello \b world\b0\par}")
    assert _one(tmp_path / "a.rtf").kind == "document"
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"x" * 50)
    assert "legacy" in _one(tmp_path / "old.doc").skipped


def test_media_inside_archive(tmp_path):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (750, 750)).save(buf, "PNG")
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("pic.png", buf.getvalue())
    r = _one(z)
    assert r.kind == "image" and r.path.endswith("!/pic.png")


def test_text_counters(tmp_path):
    (tmp_path / "t.txt").write_text("one two\nthree\n")
    r = _one(tmp_path / "t.txt")
    assert (r.lines, r.words, r.chars) == (2, 3, 14)
    from PIL import Image

    Image.new("RGB", (10, 10)).save(tmp_path / "i.png")
    assert _one(tmp_path / "i.png").chars == 0


def test_default_is_chars_over_two():
    assert Tokenizer().count("abcde") == 3  # ceil(5/2)
    assert Tokenizer().count("") == 0
