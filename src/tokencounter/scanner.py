"""Walk files, directories and archives (zip, tar, tar.gz/bz2/xz), counting tokens."""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from dataclasses import dataclass
from typing import Iterator

from . import extractors as ex
from .tokenizer import Tokenizer

MAX_MEMBER_BYTES = 20 * 1024 * 1024  # skip text/document files larger than this
MAX_TOTAL_BYTES = 500 * 1024 * 1024  # decompression-bomb guard across one archive tree
MAX_DEPTH = 3  # nested archives
SNIFF_BYTES = 8192


@dataclass
class FileResult:
    path: str
    tokens: int = 0
    size: int = 0
    skipped: str | None = None  # reason, if not counted
    kind: str = "text"  # text | document | pdf | image | audio | video
    note: str | None = None
    chars: int = 0  # text counters; 0 for images/audio/video
    words: int = 0
    lines: int = 0


class _Budget:
    def __init__(self) -> None:
        self.used = 0

    def take(self, n: int) -> bool:
        self.used += n
        return self.used <= MAX_TOTAL_BYTES


def is_text(data: bytes) -> bool:
    head = data[:SNIFF_BYTES]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # a multibyte char cut off at the sniff boundary is fine
        if e.start < len(head) - 4:
            return False
    return True


def _is_archive(name: str, data: bytes) -> str | None:
    head = data[:512]
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head[:2] == b"\x1f\x8b" or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00":
        return "tar" if _opens_as_tar(data) else None
    if head[257:262] == b"ustar" or name.lower().endswith(".tar"):
        return "tar" if _opens_as_tar(data) else None
    return None


def _opens_as_tar(data: bytes) -> bool:
    try:
        with tarfile.open(fileobj=io.BytesIO(data)):
            return True
    except (tarfile.TarError, OSError, EOFError):
        return False


def _count_bytes(path: str, data: bytes, tok: Tokenizer, depth: int, budget: _Budget) -> Iterator[FileResult]:
    size = len(data)
    dext = ex.doc_ext(path)
    try:
        if dext:  # office/odf/epub/rtf are checked before archives: docx etc. are zips
            if size > MAX_MEMBER_BYTES:
                yield FileResult(path, size=size, skipped="too large", kind="document")
                return
            kind, n, note, (c, w, l) = ex.estimate_document(dext, data, tok)
            yield FileResult(path, tokens=n, size=size, kind=kind, note=note, chars=c, words=w, lines=l)
            return
        if data.startswith(b"%PDF-"):
            kind, n, note, (c, w, l) = ex.estimate_pdf(data, tok)
            yield FileResult(path, tokens=n, size=size, kind=kind, note=note, chars=c, words=w, lines=l)
            return
    except ex.Unsupported as e:
        yield FileResult(path, size=size, skipped=str(e), kind="document")
        return

    kind = _is_archive(path, data)
    if kind:
        if depth >= MAX_DEPTH:
            yield FileResult(path, size=size, skipped="archive nested too deeply")
        else:
            yield from _scan_archive(path, data, kind, tok, depth + 1, budget)
        return
    if is_text(data):
        if size > MAX_MEMBER_BYTES:
            yield FileResult(path, size=size, skipped="too large")
        else:
            text = data.decode("utf-8", errors="replace")
            c, w, l = ex.text_stats(text)
            yield FileResult(path, tokens=tok.count(text), size=size, chars=c, words=w, lines=l)
        return
    # non-text: try image, then audio/video, else binary
    for fn in (lambda: ex.estimate_image(data), lambda: ex.estimate_av(path, data)):
        try:
            kind, n, note = fn()
            yield FileResult(path, tokens=n, size=size, kind=kind, note=note)
            return
        except ex.Unsupported as e:
            reason = str(e)
            if "could not read duration" in reason:
                yield FileResult(path, size=size, skipped=reason, kind="video")
                return
    yield FileResult(path, size=size, skipped="binary", kind="binary")


def _scan_archive(path: str, data: bytes, kind: str, tok: Tokenizer, depth: int, budget: _Budget) -> Iterator[FileResult]:
    try:
        if kind == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = f"{path}!/{info.filename}"
                    if not budget.take(info.file_size):
                        yield FileResult(name, size=info.file_size, skipped="archive size limit reached")
                        return
                    try:
                        yield from _count_bytes(name, zf.read(info), tok, depth, budget)
                    except (RuntimeError, zipfile.BadZipFile, NotImplementedError) as e:
                        yield FileResult(name, size=info.file_size, skipped=f"unreadable ({e})")
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                for member in tf:
                    if not member.isfile():  # skips dirs, symlinks, devices
                        continue
                    name = f"{path}!/{member.name}"
                    if not budget.take(member.size):
                        yield FileResult(name, size=member.size, skipped="archive size limit reached")
                        return
                    f = tf.extractfile(member)
                    if f is not None:
                        yield from _count_bytes(name, f.read(), tok, depth, budget)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as e:
        yield FileResult(path, size=len(data), skipped=f"corrupt archive ({e})")


def scan_path(root: str, tok: Tokenizer) -> Iterator[FileResult]:
    """Yield a FileResult for every file under root (file or directory), expanding archives."""
    if os.path.isfile(root):
        yield from _scan_file(root, tok)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fn in sorted(filenames):
            yield from _scan_file(os.path.join(dirpath, fn), tok)


def _scan_file(path: str, tok: Tokenizer) -> Iterator[FileResult]:
    try:
        size = os.path.getsize(path)
        if size > MAX_TOTAL_BYTES:
            yield FileResult(path, size=size, skipped="too large")
            return
        if os.path.islink(path) and not os.path.exists(path):
            yield FileResult(path, skipped="broken symlink")
            return
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        yield FileResult(path, skipped=f"unreadable ({e.strerror})")
        return
    yield from _count_bytes(path, data, tok, 0, _Budget())
