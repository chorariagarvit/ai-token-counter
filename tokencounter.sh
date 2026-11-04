#!/usr/bin/env bash
# Run tokencounter from anywhere. Creates the virtualenv on first use.
#   ./tokencounter.sh <file|dir|archive>...     count tokens
#   ./tokencounter.sh --setup                   (re)build the virtualenv
#   ./tokencounter.sh --install [DIR]           symlink as `tokencounter` into DIR (default ~/.local/bin)
set -euo pipefail

# resolve symlinks so this works when invoked through the --install link
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT="$(dirname "$SELF")"
VENV="$ROOT/.venv"

setup() {
  command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -e "$ROOT"
}

case "${1:-}" in
  --setup)
    setup
    echo "Ready: $VENV"
    exit 0
    ;;
  --install)
    dest="${2:-$HOME/.local/bin}"
    mkdir -p "$dest"
    ln -sf "$SELF" "$dest/tokencounter"
    echo "Linked $dest/tokencounter -> $SELF"
    case ":$PATH:" in *":$dest:"*) ;; *) echo "Note: $dest is not on your PATH" ;; esac
    exit 0
    ;;
  ""|-h|--help)
    [ -x "$VENV/bin/tokencounter" ] || setup
    exec "$VENV/bin/tokencounter" --help
    ;;
esac

[ -x "$VENV/bin/tokencounter" ] || setup
exec "$VENV/bin/tokencounter" "$@"
