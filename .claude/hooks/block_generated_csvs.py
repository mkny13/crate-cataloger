#!/usr/bin/env python3
"""PreToolUse guard: block edits to generated added-*.csv / dupes-*.csv.

Exit 2 blocks the tool call; any error in this hook allows it (never wedge a session).
"""
import fnmatch
import json
import os
import re
import sys

PATTERNS = ("added-*.csv", "dupes-*.csv")


def check_bash_command(command: str):
    """Return matching CSV target if command mutates added-*.csv or dupes-*.csv, else None."""
    if not command or ("added-" not in command and "dupes-" not in command):
        return None

    # Split into subcommands around command separators and pipes
    subcmds = re.split(r'&&|\|\||[;&|]', command)
    for subcmd in subcmds:
        subcmd = subcmd.strip()
        if not subcmd:
            continue

        csv_match = re.search(r'(?:^|[\s"\'/])(added-[^\s"\']*?\.csv|dupes-[^\s"\']*?\.csv)', subcmd)
        if not csv_match:
            continue
        target = csv_match.group(1)

        # 1. Output redirection to CSV: >, >>, >|
        if re.search(r'>[|]?\s*[\'"]?(?:[^\s\'"]*/)?(?:added-[^\s\'"]*\.csv|dupes-[^\s\'"]*\.csv)', subcmd):
            return target

        # 2. In-place edits with sed or perl (-i, --in-place)
        if re.search(r'\b(?:sed|perl)\b', subcmd) and re.search(r'(?:^|\s)(?:-i\S*|--in-place\S*)', subcmd):
            return target

        # 3. awk processing/modifying CSV
        if re.search(r'\bawk\b', subcmd):
            return target

        # 4. tee command writing to CSV
        if re.search(r'\btee\b', subcmd):
            return target

        # 5. cp or mv where CSV is the destination (last argument of subcmd)
        if re.search(r'\b(?:cp|mv)\b\s+.*?(?:[^\s\'"]*/)?[\'"]?(?:added-[^\s\'"]*\.csv|dupes-[^\s\'"]*\.csv)[\'"]?\s*$', subcmd):
            return target

        # 6. touch or truncate targeting CSV
        if re.search(r'\b(?:touch|truncate)\b', subcmd):
            return target

    return None


def main():
    try:
        data = json.load(sys.stdin)
        inp = data.get("tool_input") or {}

        # 1. Check file editing tool paths
        paths = []
        for k in ("file_path", "notebook_path", "path"):
            val = inp.get(k)
            if val:
                paths.append(str(val))
        if isinstance(inp.get("paths"), list):
            paths.extend(str(x) for x in inp["paths"])
        if isinstance(inp.get("edits"), list):
            for edit in inp["edits"]:
                if isinstance(edit, dict):
                    for k in ("file_path", "notebook_path", "path"):
                        val = edit.get(k)
                        if val:
                            paths.append(str(val))

        for p_str in paths:
            name = os.path.basename(p_str)
            if any(fnmatch.fnmatch(name, pat) for pat in PATTERNS):
                print(f"Blocked: {name} is a generated crate.py output; don't edit it by hand. "
                      "Re-run the command that produced it (or `crate.py undo`) instead.",
                      file=sys.stderr)
                sys.exit(2)

        # 2. Check Bash tool command
        command = inp.get("command") or data.get("command") or ""
        if command:
            target = check_bash_command(str(command))
            if target:
                print(f"Blocked: {target} is a generated crate.py output; don't edit it by hand. "
                      "Re-run the command that produced it (or `crate.py undo`) instead.",
                      file=sys.stderr)
                sys.exit(2)

    except SystemExit:
        raise
    except Exception:
        pass


if __name__ == "__main__":
    main()

