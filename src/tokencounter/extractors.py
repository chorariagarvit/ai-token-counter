"""Per-type token estimation for documents, PDFs, images, audio and video.

Each estimator returns (kind, tokens, note) or raises Unsupported(reason).
Rates for media follow published vendor guidance and are estimates:
  - images: Claude formula, tokens ~= w*h/750 after downscaling to 1568px, capped ~1600
  - audio: 32 tokens/sec, video: 263 tokens/sec (Gemini rates; Claude takes neither)
"""

from __future__ import annotations

import html
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from .tokenizer import Tokenizer

IMAGE_MAX_EDGE = 1568
IMAGE_MAX_TOKENS = 1600
AUDIO_TOKENS_PER_SEC = 32
VIDEO_TOKENS_PER_SEC = 263
MAX_PDF_PAGES = 5000
MAX_XML_BYTES = 50 * 1024 * 1024  # per XML part inside an office file

ZIP_DOC_EXTS = {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm", ".odt", ".ods", ".odp", ".epub"}
LEGACY_OFFICE_EXTS = {".doc", ".xls", ".ppt"}


class Unsupported(Exception):
    pass


def text_stats(text: str) -> tuple[int, int, int]:
    """(characters, words, lines)"""
    return len(text), len(text.split()), len(text.splitlines())


def image_tokens(w: int, h: int) -> int:
    scale = min(1.0, IMAGE_MAX_EDGE / max(w, h, 1))
    w, h = w * scale, h * scale
    return min(IMAGE_MAX_TOKENS, max(1, -(-int(w * h) // 750)))


# ---------- documents ----------

_TAG = re.compile(r"<[^>]+>")


def _strip_xml(xml: str, para_tags: tuple[str, ...]) -> str:
    for t in para_tags:
        xml = re.sub(rf"</{t}\s*>", "\n", xml)
    xml = re.sub(r"<(?:w:tab|w:br|text:tab|text:line-break)\b[^>]*/>", " ", xml)
    return html.unescape(_TAG.sub("", xml))


def _read_part(zf: zipfile.ZipFile, name: str) -> str:
    info = zf.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise Unsupported(f"{name} too large")
    return zf.read(name).decode("utf-8", errors="replace")


def _xlsx_text(zf: zipfile.ZipFile) -> str:
    names = set(zf.namelist())
    sst: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in re.findall(r"<si\b[^>]*>(.*?)</si>", _read_part(zf, "xl/sharedStrings.xml"), re.S):
            sst.append(html.unescape(_TAG.sub("", si)))
    out: list[str] = []
    for n in sorted(x for x in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", x)):
        for row in re.findall(r"<row\b[^>]*>(.*?)</row>", _read_part(zf, n), re.S):
            cells = []
            for attrs, body in re.findall(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                v = re.search(r"<v>(.*?)</v>", body or "", re.S)
                if 't="s"' in attrs and v and v.group(1).isdigit() and int(v.group(1)) < len(sst):
                    cells.append(sst[int(v.group(1))])
                elif 't="inlineStr"' in attrs:
                    cells.append(html.unescape(_TAG.sub("", body or "")))
                elif v:
                    cells.append(html.unescape(v.group(1)))
            out.append("\t".join(cells))
    return "\n".join(out)


def extract_zip_document(ext: str, data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise Unsupported(f"corrupt {ext} ({e})")
    with zf:
        names = zf.namelist()
        if ext in (".docx", ".docm"):
            parts = [n for n in names if re.fullmatch(r"word/(document|footnotes|endnotes|header\d*|footer\d*)\.xml", n)]
            return "\n".join(_strip_xml(_read_part(zf, n), ("w:p",)) for n in sorted(parts))
        if ext in (".pptx", ".pptm"):
            def num(n: str) -> int:
                return int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1))
            parts = [n for n in names if re.fullmatch(r"ppt/(slides|notesSlides)/\w+\.xml", n)]
            return "\n".join(_strip_xml(_read_part(zf, n), ("a:p",)) for n in sorted(parts, key=lambda n: (n.split("/")[1], num(n))))
        if ext in (".xlsx", ".xlsm"):
            return _xlsx_text(zf)
        if ext in (".odt", ".ods", ".odp"):
            return _strip_xml(_read_part(zf, "content.xml"), ("text:p", "text:h", "table:table-row"))
        if ext == ".epub":
            parts = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]
            chunks = []
            for n in sorted(parts):
                s = re.sub(r"<(script|style)\b.*?</\1>", "", _read_part(zf, n), flags=re.S | re.I)
                chunks.append(_strip_xml(s, ("p", "div", "h1", "h2", "h3", "li")))
            return "\n".join(chunks)
    raise Unsupported(f"unknown document type {ext}")


def extract_rtf(data: bytes) -> str:
    s = data.decode("latin-1")
    s = re.sub(r"\{\\\*[^{}]*\}", "", s)  # ignorable destinations
    s = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "replace"), s)
    s = re.sub(r"\\(?:par|line)\b ?", "\n", s)
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", s)
    return re.sub(r"[{}\\]", "", s)


def estimate_document(ext: str, data: bytes, tok: Tokenizer):
    if ext in LEGACY_OFFICE_EXTS:
        raise Unsupported(f"legacy binary Office format ({ext}); convert to {ext}x")
    text = extract_rtf(data) if ext == ".rtf" else extract_zip_document(ext, data)
    return "document", tok.count(text), None, text_stats(text)


# ---------- PDF ----------

def estimate_pdf(data: bytes, tok: Tokenizer):
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unsupported("encrypted PDF")
        pages = reader.pages
        n = len(pages)
        text_tokens = img_tokens = 0
        chars = words = lines = 0
        for i in range(min(n, MAX_PDF_PAGES)):
            page = pages[i]
            try:
                page_text = page.extract_text() or ""
                text_tokens += tok.count(page_text)
                c, w_, l = text_stats(page_text)
                chars, words, lines = chars + c, words + w_, lines + l
            except Exception:  # one bad page shouldn't sink the file
                pass
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            img_tokens += image_tokens(int(w), int(h))  # 1pt ~ 1px page render
    except Unsupported:
        raise
    except (PyPdfError, Exception) as e:  # noqa: BLE001 - pypdf raises many types on bad input
        raise Unsupported(f"unreadable PDF ({type(e).__name__})")
    note = f"{n} pages: {text_tokens:,} text + {img_tokens:,} page-image"
    return "pdf", text_tokens + img_tokens, note, (chars, words, lines)


# ---------- images ----------

def estimate_image(data: bytes) -> tuple[str, int, str | None]:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:  # header only; pixels are not decoded
            w, h = im.size
    except Exception as e:  # noqa: BLE001
        raise Unsupported(f"not a recognised image ({type(e).__name__})")
    return "image", image_tokens(w, h), f"{w}x{h}"


# ---------- audio / video ----------

def _ffprobe(data: bytes) -> tuple[float, bool] | None:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run(
                [exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", f.name],
                capture_output=True, text=True, timeout=30, check=True,
            ).stdout
            info = json.loads(out)
            dur = float(info["format"]["duration"])
        except (subprocess.SubprocessError, KeyError, ValueError, OSError):
            return None
    has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    return dur, has_video


def estimate_av(name: str, data: bytes) -> tuple[str, int, str | None]:
    mime = mimetypes.guess_type(name)[0] or ""
    duration = None
    try:
        import mutagen

        mf = mutagen.File(io.BytesIO(data))
        if mf is not None and mf.info is not None and getattr(mf.info, "length", 0):
            duration = float(mf.info.length)
    except Exception:  # noqa: BLE001
        pass
    is_video = mime.startswith("video/")
    if duration is None:
        probed = _ffprobe(data)
        if probed is None:
            if mime.startswith(("audio/", "video/")):
                raise Unsupported("could not read duration (install ffmpeg/ffprobe for more formats)")
            raise Unsupported("binary")
        duration, is_video = probed[0], probed[1] or is_video
    if is_video:
        return "video", int(duration * VIDEO_TOKENS_PER_SEC), f"{duration:.1f}s @ {VIDEO_TOKENS_PER_SEC}/s"
    return "audio", int(duration * AUDIO_TOKENS_PER_SEC), f"{duration:.1f}s @ {AUDIO_TOKENS_PER_SEC}/s"


def is_zip_document(name: str) -> bool:
    return os.path.splitext(name.lower())[1] in ZIP_DOC_EXTS


def doc_ext(name: str) -> str | None:
    ext = os.path.splitext(name.lower())[1]
    return ext if ext in ZIP_DOC_EXTS | LEGACY_OFFICE_EXTS | {".rtf"} else None
