#!/usr/bin/env python3
"""PreToolUse guard: block edits to generated added-*.csv / dupes-*.csv.

Exit 2 blocks the tool call; any error in this hook allows it (never wedge a session).
"""
import fnmatch
import json
import os
import sys

PATTERNS = ("added-*.csv", "dupes-*.csv")


def main():
    try:
        data = json.load(sys.stdin)
        inp = data.get("tool_input") or {}
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        name = os.path.basename(str(path))
        if any(fnmatch.fnmatch(name, p) for p in PATTERNS):
            print(f"Blocked: {name} is a generated crate.py output; don't edit it by hand. "
                  "Re-run the command that produced it (or `crate.py undo`) instead.",
                  file=sys.stderr)
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        pass


if __name__ == "__main__":
    main()
