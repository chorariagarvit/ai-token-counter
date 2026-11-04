"""Token counting. Default is a conservative chars/2 estimate; tiktoken encodings are opt-in."""

from __future__ import annotations

CHARS_PER_TOKEN = 2  # deliberately pessimistic: Claude measured ~2.5 chars/token on technical markdown
DEFAULT_ENCODING = f"chars/{CHARS_PER_TOKEN}"


class Tokenizer:
    def __init__(self, encoding: str = DEFAULT_ENCODING) -> None:
        self.encoding_name = encoding
        self.exact = False  # True only when a real tokenizer (tiktoken) is in use
        self._enc = None
        if encoding == DEFAULT_ENCODING:
            return
        try:
            import tiktoken

            self._enc = tiktoken.get_encoding(encoding)
            self.exact = True
        except Exception:  # missing package, unknown encoding, or vocab download failed (offline)
            self.encoding_name = f"{DEFAULT_ENCODING} (fallback: {encoding} unavailable)"

    def count(self, text: str) -> int:
        if self._enc is None:
            return -(-len(text) // CHARS_PER_TOKEN)
        # disallowed_special=() so literal text like "<|endoftext|>" is counted, not rejected
        return len(self._enc.encode(text, disallowed_special=()))
