# tokencounter

Estimate how many LLM tokens a file, directory, zip or tarball will use when sent to an AI.

## Install

```bash
./tokencounter.sh --setup            # creates .venv and installs dependencies
./tokencounter.sh --install          # optional: symlink `tokencounter` into ~/.local/bin
```

The first run of `tokencounter.sh` does the setup automatically. Requires Python 3.10+.
Text is counted with a deliberately conservative rule of 1 token per 2 characters, so it works
offline. Pass `-e o200k_base` (or another tiktoken encoding) for a real GPT tokenizer count; `tiktoken`
downloads its vocabulary on first use, and if that fails the tool falls back to chars/2 and says so.

## Usage

```bash
./tokencounter.sh report.pdf
./tokencounter.sh ~/Projects/my-app                 # directory (.git is skipped)
./tokencounter.sh site.zip backup.tar.gz notes.md   # several inputs at once
./tokencounter.sh -a site.zip                       # list every file
./tokencounter.sh --json site.zip > tokens.json
./tokencounter.sh -e o200k_base file.txt            # use a real tiktoken encoding instead of chars/2
```

### Estimate output tokens

Output tokens are counted with the same tokenizer as input, but their length depends on the task, so
pass `-o TASK` to get an estimated range from the input size:

```bash
./tokencounter.sh -o summarize report.pdf     # ~5-25% of the input
./tokencounter.sh -o review src/              # ~10-60%   (explain / analyze / code review)
./tokencounter.sh -o rewrite doc.md           # ~90-150%  (translate / convert / return whole file)
./tokencounter.sh -o classify ticket.txt      # fixed ~5-100 tokens
./tokencounter.sh -o 0.4 notes.txt            # custom multiple of the input
```

These ratios are rough heuristics, not measurements, and are applied to the text/document/PDF input
tokens (which are already on the high side). Extended thinking and tool calls add output tokens on top.

### Count typed or pasted text (chat-style)

```bash
./tokencounter.sh -t "How many tokens is this message?"   # one-off
cat prompt.txt | ./tokencounter.sh -                       # from stdin
./tokencounter.sh -i                                       # interactive chat counter
```

In `-i` mode, type or paste text and press Enter: each line is counted immediately (tokens, words, chars
and lines) with a running conversation total. A pasted multi-line block is counted line by line. Ctrl-D quits.

Output lists the top 20 files by tokens (all with `-a`) with lines, words and chars, skipped files
with the reason, a per-type subtotal, text totals and the grand total.

## What is supported

| Input | How tokens are estimated |
|---|---|
| Text / code (any extension) | characters / 2, rounded up (conservative; `-e` switches to tiktoken) |
| PDF | text tokens + a page-image cost per page |
| `.docx .pptx .xlsx .odt .ods .odp .epub .rtf` | extracted text, then tokenized |
| Images (png, jpg, gif, webp, ...) | Claude rule: width x height / 750, long edge scaled to 1568px, capped at 1,600 |
| Audio | duration x 32 tokens/s (Gemini rate) |
| Video | duration x 263 tokens/s (Gemini rate) |
| `.zip .tar .tar.gz .tar.bz2 .tar.xz` | expanded in memory, every member counted; nested up to 3 levels |

Archives and office documents are detected by content/extension, not just the archive name.

## Limits and skipped files

- Binary files with no known format are skipped (reported as `binary`).
- Legacy `.doc/.xls/.ppt` are skipped; convert to `.docx/.xlsx/.pptx`.
- Text and documents over 20 MB are skipped; an archive tree stops at 500 MB uncompressed
  (zip-bomb guard); encrypted or corrupt files are reported, not fatal.
- Audio/video duration comes from `mutagen` (mp3, mp4, m4a, wav, flac, ogg, ...). For mkv, avi,
  webm and others install `ffmpeg` so `ffprobe` is available.

## Accuracy

These are estimates, tuned to err high. Text uses 1 token per 2 characters. Measured on three technical
markdown files (24K chars), Claude's real count was 9,830 tokens (about 2.5 chars/token), while chars/2
gives ~12,100, roughly 23% above. Prose with common English words runs closer to 4 chars/token, so
chars/2 can overshoot by 2x there; CJK, code and symbol-heavy text can come close to chars/2. Images and
PDF page images use Claude's published rule; audio/video rates are Gemini's, since Claude does not
accept those types. Anthropic's `count_tokens` endpoint gives exact figures.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Layout: `src/tokencounter/` - `tokenizer.py` (text counting), `extractors.py` (documents, PDF, image,
audio/video, text stats), `scanner.py` (files, directories, archives), `cli.py`.
