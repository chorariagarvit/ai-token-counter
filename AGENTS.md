# tokencounter

Python CLI that estimates the LLM tokens a file, directory, zip or tarball would use when sent to an AI.
Also counts typed/pasted text (`-t`, `-`, `-i`). See README.md for user-facing docs.

## Commands

```bash
./tokencounter.sh --setup                  # create .venv and install deps (editable)
./tokencounter.sh <path>...                # run the CLI (auto-setup on first run)
.venv/bin/pytest -q                    # run tests (fast, no network except tiktoken vocab cache)
```

Python >= 3.10. Dependencies: tiktoken, pillow, pypdf, mutagen. Optional: `ffprobe` (ffmpeg) for mkv/avi/webm duration.

## Layout

- `src/tokencounter/tokenizer.py` - `Tokenizer`: default is conservative chars/2 (offline, `exact=False`); `-e <encoding>` opts into tiktoken (`exact=True`), falling back to chars/2 if it can't load.
- `src/tokencounter/extractors.py` - per-type estimators (documents, PDF, image, audio/video), `text_stats`, `Unsupported`.
- `src/tokencounter/scanner.py` - `scan_path`, `FileResult`; walks dirs and expands zip/tar(.gz/.bz2/.xz) in memory.
- `src/tokencounter/cli.py` - argparse entry point, report formatting, text/stdin/interactive modes.
- `tests/test_scanner.py` - builds fixtures on the fly in `tmp_path`; no checked-in binary fixtures.

## Conventions and gotchas

- Dispatch order in `scanner._count_bytes` matters: office/epub/rtf by extension first (docx etc. are zips),
  then PDF magic, then archives, then text, then image, then audio/video, else binary.
- Extractors raise `extractors.Unsupported(reason)`; the scanner turns that into a skipped `FileResult`. Never let a
  bad file abort the whole scan.
- Archive inputs are untrusted. Keep the guards: `MAX_TOTAL_BYTES` (decompression bomb), `MAX_DEPTH` (nesting),
  `MAX_MEMBER_BYTES` (text/docs). Never extract to disk; read members in memory. Office XML is parsed with regex,
  not an XML parser, to avoid entity-expansion attacks.
- Everything here is an estimate, not billing truth. Measured on 3 technical markdown files: Claude real tokens were
  ~2.5 chars/token, so chars/2 is intentionally ~25% high (the user asked for the safe side). Do not claim exactness in output or docs.
- Image tokens use the Claude rule (w*h/750, long edge 1568, cap 1600). Audio/video use Gemini rates (32/s, 263/s)
  because Claude accepts neither. Keep the note text in `cli.py` consistent with this if rates change.
- New file types: add an estimator in `extractors.py`, wire it in `_count_bytes`, add a test that builds a tiny
  fixture programmatically.
- Match the existing style: type hints, `from __future__ import annotations`, short comments only for non-obvious why.
