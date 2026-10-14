#!/usr/bin/env python3
import sys
import os
import json
import subprocess

def main():
    project = "crate-cataloger"
    run_id = os.environ.get("MAHLER_RUN_ID")
    
    if run_id:
        # MAHLER_RUNS_DIR lets tests redirect the runs dir; production
        # leaves it unset and gets the real state dir (mahler#93).
        runs_dir = os.environ.get("MAHLER_RUNS_DIR") or os.path.expanduser("~/.mahler/runs")
        yield_file = os.path.join(runs_dir, str(run_id), "yield")
        if os.path.exists(yield_file):
            print("yield delivered: commit your work, push the branch, and end with STATUS: YIELDED")
            sys.exit(2)
            
    tool_args = {}
    if not sys.stdin.isatty():
        try:
            tool_args = json.load(sys.stdin)
        except Exception:
            pass
            
    if run_id:
        command = tool_args.get("command", "")
        # D6: fence outward-facing steps — `gh pr create|merge` — on the epoch.
        if command and ("gh pr merge" in command or "gh pr create" in command):
            res = subprocess.run(["mahler", "lease-check"], capture_output=True, text=True)
            if res.returncode != 0:
                print(res.stdout.strip())
                sys.exit(2)
                
    if not run_id:
        # Nudge once per session if unclaimed
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", str(os.getpid()))
        # Per-user 0700 dir, not a predictable world-writable /tmp path.
        state_dir = os.environ.get("MAHLER_NUDGE_DIR") or os.path.expanduser("~/.mahler/nudge")
        safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64] or "unknown"
        state_file = os.path.join(state_dir, safe_id)
        if not os.path.lexists(state_file):
            try:
                out = subprocess.check_output(["mahler", "status", "--project", project, "--json"], text=True)
                d = json.loads(out)
                my_holder = f"interactive:{session_id[:8]}"
                holds_claim = any(l.get("holder") == my_holder
                                  or l.get("holder", "").endswith("/" + my_holder)
                                  for l in d.get("leases", []))
                if not holds_claim:
                    print(f"If this work relates to a backlog item, run `mahler claim {project}#N`.")
                    os.makedirs(state_dir, mode=0o700, exist_ok=True)
                    os.chmod(state_dir, 0o700)
                    fd = os.open(state_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    os.close(fd)
            except Exception:
                pass

if __name__ == "__main__":
    main()
