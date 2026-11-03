# Architecture & specification drift audit — crate-cataloger #41

Audit of the documents that actually describe the app — `README.md`, `SECURITY.md`, `AGENTS.md` and
the `crate.py` module docstring — against the code in `crate.py`. `DESIGN.md`, `ARCHITECTURE.md` and
`ROADMAP.md` do not and will not exist in this repo; the audit runs against the documents that exist.

## Results summary

| # | Drift item | Verdict | Reason |
|---|-----------|---------|--------|
| 1 | `SECURITY.md` stale `crate.py:NNN` line refs → symbol references | **fixed** | All 23 stale refs replaced with the pointed-to symbol (e.g. `host_ok` now `crate.py:533`, `spell_fix` now `crate.py:267`, `resolve` now `crate.py:342`, `csv_safe` now `crate.py:325`, `cmd_undo` now `crate.py:716`, `AddLog` now `crate.py:359`); verified by `tests/test_docs.py` |
| 2 | README missing long flags (`add -y/--yes`, `serve --port`, `serve --folder`; Notes `--folder` scope limited to `quick`/`add`) | **fixed** | Documented `--yes` for `add`, `--port` (default 8765) and `--folder` for `serve`, and widened `--folder` to `quick`/`add`/`serve` |
| 3 | `AGENTS.md` "Verify" matched only `py_compile`; CI also runs `unittest discover -s tests` | **fixed** | Verify section now lists both commands, matching `.github/workflows/ci.yml` |
| 4 | README Undo line claimed only `add` writes `added-<timestamp>.csv` | **fixed** | Undo line now names `quick`, `add` and `serve` as log writers; serve log wording corrected |
| 5 | Zombie abstractions | **OK** | AST scan of every top-level `def`/`class`/const/import against references in `crate.py` and `tests/` found no dead code; nothing removed |

## Zombie-abstraction check detail

Method (plan step 5): for each `def`/`class`/top-level constant/import in `crate.py`, checked
word-boundary references across `crate.py` plus `tests/`, excluding each name's own definition line.

Result: **no zombies found — no code removed**. Live names verified by kind:

- **Top-level constants:** `API`, `UA`, `TOKEN_FILE`, `DEFAULT_FOLDER`, `N_CANDIDATES`, `FIX_BELOW`,
  `CONFIDENT`, `MARGIN`, `MAX_BODY`, `PAGE`.
- **Stdlib imports:** `argparse`, `csv`, `difflib`, `getpass`, `hmac`, `json`, `os`, `secrets`,
  `socket`, `threading`, `re`, `sys`, `time`, `unicodedata`, `urllib.error`, `urllib.parse`,
  `urllib.request`, `datetime`, `BaseHTTPRequestHandler`, `ThreadingHTTPServer`, `Path`.
- **Functions and classes:** `_NoRedirect`, `_id`, `Discogs`, `norm`, `split_line`, `similarity`,
  `find_hinted`, `_dedupe`, `find`, `spell_fix`, `_search`, `is_confident`, `csv_safe`, `label`,
  `owned`, `resolve`, `AddLog`, `cmd_setup`, `client`, `_quick_pick`, `cmd_quick`, `cmd_match`,
  `cmd_serve`, `cmd_undo`, `_dupe_groups`, `_dupe_row`, `cmd_dupes`, `main`, `lan_ip`, `host_ok`,
  `build_parser`.

`_NoRedirect` is a `urllib.request.HTTPRedirectHandler` override (its `redirect_request` returns
None), so it is live via `build_opener`. `build_parser()` was extracted from `main()` purely to make
the CLI options checkable without changing behaviour.
