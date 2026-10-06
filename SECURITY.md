# Security & surface area audit

Scope: `crate.py` (the whole app; one stdlib-only Python file). Regression tests: `tests/test_security.py`
(`python3 -m unittest discover -s tests -v`, no network or token needed).

Verdicts: **OK**, **fixed** (in this change), **n/a** (not applicable, with reason).

## 1. Credential storage — OK
The token file is created with mode `0o600` in a `0o700` directory and re-`chmod`ed on every setup
(`crate.py:384-395`, `TOKEN_FILE` at `crate.py:42`). `DISCOGS_TOKEN` env var is an optional fallback
(`crate.py:398-402`). There is no `.env` loading. `.gitignore` covers `.env`, `token`, `*.pem` and
run-output CSVs. Test: `SetupPerms`.

## 2. Credential transmission — OK
The `Authorization` header is only built in `Discogs.call` (`crate.py:79-92`), where the URL is always
`API + path` with `API = "https://api.discogs.com"` (`crate.py:40`). `_NoRedirect` (`crate.py:53`) stops
urllib re-sending the header to a redirect target. The iTunes typo lookup (`spell_fix`, `crate.py:259`)
uses a plain `urlopen` with no token. Every outbound host is hardcoded (`api.discogs.com`,
`itunes.apple.com`); no user input changes the host. Test: `NoRedirect`.

## 3. Subprocess / shell execution — n/a
The scoped application is the single module `crate.py:1`.
`grep -nE "subprocess|os\.system|os\.popen|eval\(|exec\(" crate.py` returns nothing. The app never
spawns a process, so there are no arguments to sanitize.

## 4. `serve` web surface — OK
- Per-run 128-bit key (`secrets.token_urlsafe(16)`) carried in the URL fragment, so it never reaches
  server logs or Referer headers; compared with `hmac.compare_digest` (`_authed`).
- Host-header allowlist against DNS rebinding: `host_ok` (`crate.py:524`), now module-level and tested.
- `MAX_BODY` (4096) cap on POST bodies (`crate.py:48`, `crate.py:589`).
- Response headers: CSP, `nosniff`, `no-referrer`, `no-store` (`crate.py:545-552`).
- The page never uses `innerHTML`; all dynamic content goes through `textContent`.
- Default bind is `0.0.0.0` over plain HTTP so a phone on the LAN can reach it. This is a documented
  product choice (README); `--host 127.0.0.1` restricts it. Left as is.
- Open item (not changed): no rate limiting on wrong-key attempts. A 128-bit key makes guessing
  infeasible.
Test: `HostOk`.

## 5. URL path construction from untrusted input — fixed
`resolve()` (`crate.py:341`) only returns ints. The gap was `undo`: `cmd_undo` (`crate.py:706`) passed
`release_id`, `folder` and `instance_id` from a user-supplied CSV straight into the DELETE path, so a
tampered `added-*.csv` (e.g. `folder` = `1/../../..`) could send an authenticated DELETE to an
unintended Discogs path. Now `Discogs.remove` and `Discogs.add` (`crate.py:169-178`) pass every id
through `_id()` (`crate.py:62`), which requires a whole number (`>= 1`; folder `>= 0` for remove,
`>= 1` for add) and raises `ValueError` before any network call. `cmd_undo` reports such rows as
`FAILED <input>: <reason>` and continues. Tests: `Resolve`, `IdValidation`.

## 6. CSV formula injection — OK
`csv_safe` (`crate.py:324`) prefixes `=`, `+`, `-`, `@`, tab and CR with `'`. It is applied to every
Discogs-supplied or user-typed cell written to CSV: `AddLog.write` (`crate.py:373`), `cmd_match`
(`crate.py:659`) and `cmd_dupes` (`crate.py:747-749`). Numeric ids and counts are not wrapped. Test:
`CsvSafe`.

## 7. Personal data in output files — OK
`added-*.csv`, `dupes-*.csv` and `*.review.csv` hold your collection and are git-ignored. They are
created by `AddLog` (`crate.py:361`), `cmd_match` (`crate.py:633`) and `cmd_dupes`
(`crate.py:735`).

## 8. Dependency scan — n/a
Stdlib only; there is no `requirements.txt` or `pyproject.toml`, so nothing to scan. Needs Python 3.8+
(see the `crate.py` docstring, `crate.py:5`).

## 9. Denial lists / permission boundaries — n/a
There is no deny-list or role system. The boundaries that exist are the `serve` key
(`crate.py:13`) and the Host check (`host_ok`, `crate.py:524-527`; item 4), plus the token's own
Discogs permissions.
