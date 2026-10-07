# crate-cataloger

## Verify

Run before every push (PR CI runs the same command):

```sh
python3 -m py_compile crate.py
```

Tests: `python3 -m unittest discover -s tests`

## Working with Mahler

Mahler conducts coding agents through this repo's GitHub-issue backlog.

- Start from an issue, then run `mahler claim crate-cataloger#N` before writing code.
- Work on branch `mahler/<N>-short-slug` in your own worktree; never switch branches in the primary checkout.
- Run `mahler heartbeat crate-cataloger#N` during long runs so the claim stays live.
- Run the verify command above, then commit and push the branch at each checkpoint.
- Put `Fixes #N` in any PR.
- Once the branch is pushed and verified, run `mahler ship crate-cataloger#N`. The conductor opens the PR, reviews it, watches CI and merges on green. Do not leave a PR open.
- To abandon work, run `mahler release crate-cataloger#N`.
- Autonomous runs end their final message with a `STATUS:` line (`DONE`, `NEEDS-YOU`, `BLOCKED` or `YIELDED`).
