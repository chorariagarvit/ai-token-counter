from __future__ import annotations

import argparse
import json
import sys

from .extractors import text_stats
from .scanner import FileResult, scan_path
from .tokenizer import DEFAULT_ENCODING, Tokenizer


def _report_text(text: str, tok: Tokenizer, as_json: bool = False) -> int:
    tokens = tok.count(text)
    lines, words, chars = (lambda c, w, l: (l, w, c))(*text_stats(text))
    if as_json:
        json.dump({"encoding": tok.encoding_name, "exact": tok.exact, "tokens": tokens,
                   "chars": chars, "words": words, "lines": lines}, sys.stdout, indent=2)
        print()
    else:
        approx = "" if tok.exact else " (safe estimate)"
        print(f"Tokens: {tokens:,}{approx}   Words: {words:,}   Chars: {chars:,}   Lines: {lines:,}")
    return tokens


def _interactive(tok: Tokenizer) -> int:
    """Chat-style counter: every Enter counts that line; Ctrl-D (or Ctrl-C) quits."""
    print(f"tokencounter chat counter ({tok.encoding_name}). Type text and press Enter to count it.")
    print("A pasted multi-line block is counted line by line. Ctrl-D to quit.\n")
    running = 0
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        running += _report_text(line, tok)
        print(f"  Conversation total: {running:,} tokens\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tokencounter", description="Count LLM tokens in files, directories, zips and tarballs.")
    p.add_argument("paths", nargs="*", help="files, directories or archives ('-' reads text from stdin)")
    p.add_argument("-t", "--text", help="count tokens in this text instead of files")
    p.add_argument("-i", "--interactive", action="store_true", help="chat-style counter: paste text, Ctrl-D to count")
    p.add_argument("-e", "--encoding", default=DEFAULT_ENCODING, help=f"'{DEFAULT_ENCODING}' (default, conservative: 1 token per 2 chars) or a tiktoken encoding such as o200k_base")
    p.add_argument("-a", "--all", action="store_true", help="list every file, not just the top 20 by tokens")
    p.add_argument("--json", action="store_true", help="emit JSON")
    args = p.parse_args(argv)

    tok = Tokenizer(args.encoding)
    if args.interactive:
        return _interactive(tok)
    if args.text is not None:
        _report_text(args.text, tok, args.json)
        return 0
    if args.paths == ["-"]:
        _report_text(sys.stdin.read(), tok, args.json)
        return 0
    if not args.paths:
        p.error("give a path, -t TEXT, '-' for stdin, or -i for interactive mode")
    results: list[FileResult] = []
    for path in args.paths:
        results.extend(scan_path(path, tok))

    counted = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]
    total = sum(r.tokens for r in counted)

    if args.json:
        json.dump(
            {
                "encoding": tok.encoding_name,
                "exact": tok.exact,
                "total_tokens": total,
                "files": [r.__dict__ for r in results],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if counted:
        print(f"{'tokens':>12} {'lines':>8} {'words':>9} {'chars':>10}  type     path")
    shown = sorted(counted, key=lambda r: r.tokens, reverse=True)
    if not args.all:
        shown = shown[:20]
    for r in shown:
        extra = f"  ({r.note})" if r.note else ""
        counts = f"{r.lines:>8,} {r.words:>9,} {r.chars:>10,}" if r.chars else f"{'-':>8} {'-':>9} {'-':>10}"
        print(f"{r.tokens:>12,} {counts}  {r.kind:<8} {r.path}{extra}")
    if len(shown) < len(counted):
        print(f"{'...':>12}  ({len(counted) - len(shown)} more, use --all)")
    for r in skipped:
        print(f"{'skipped':>12}  {r.path} [{r.skipped}]")
    by_kind: dict[str, int] = {}
    for r in counted:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + r.tokens
    if len(by_kind) > 1:
        print("\nBy type: " + ", ".join(f"{k} {v:,}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])))
    print(
        f"\nText totals:   {sum(r.lines for r in counted):,} lines, "
        f"{sum(r.words for r in counted):,} words, {sum(r.chars for r in counted):,} chars"
    )
    print(f"Files counted: {len(counted)}  skipped: {len(skipped)}")
    print(f"Total tokens:  {total:,}  ({tok.encoding_name}{'' if tok.exact else ', safe estimate'})")
    print("Note: estimates. Text assumes 1 token per 2 chars (conservative; use -e o200k_base for a GPT count); images/PDF pages use Claude's")
    print("      w*h/750 rule; audio/video use Gemini rates (32/s, 263/s) - Claude does not accept audio/video.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
