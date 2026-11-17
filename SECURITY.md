# Security & surface area audit

Scope: `crate.py`, `.claude/hooks/*.py`, `.claude/settings.json`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `scripts/apply-branch-protection.sh`. Regression tests: `tests/test_security.py`
(`python3 -m unittest discover -s tests -v`, no network or token needed).

Verdicts: **OK**, **fixed** (in this change), **n/a** (not applicable, with reason).

## 1. Credential storage — OK
The token file is created with mode `0o600` in a `0o700` directory and re-`chmod`ed on every setup (`cmd_setup`, `TOKEN_FILE`). `DISCOGS_TOKEN` env var is an optional fallback (`client`). There is no `.env` loading. `.gitignore` covers `.env`, `token`, `*.pem` and run-output CSVs. Test: `SetupPerms`.

## 2. Credential transmission — OK
The `Authorization` header is only built in `Discogs.call`, where the URL is always
`API + path` with `API = "https://api.discogs.com"`. `_NoRedirect` (urllib override) stops
urllib re-sending the header to a redirect target. The iTunes typo lookup (`spell_fix`) uses a plain `urlopen` with no token. Every outbound host is hardcoded (`api.discogs.com`,
`itunes.apple.com`); no user input changes the host. Other `https://` literals in `crate.py` are the User-Agent, the token-creation URL printed by `cmd_setup`, the CSP image host and release links in output, none used as a request base. Tests: `NoRedirect`, `OutboundHosts`.

## 3. Subprocess / shell execution — OK
`crate.py` spawns nothing: `grep -nE "subprocess|os\.system|os\.popen|eval\(|exec\(" crate.py` returns nothing.
The hooks do spawn `mahler`, always with list-form argv and no `shell=True`, `os.system` or string command, so arguments cannot be shell-injected:
- `pre_tool_use.py`: `["mahler", "lease-check"]` (hardcoded); `["mahler", "status", "--project", project, "--json"]` (`project` is a hardcoded string).
- `heartbeat.py`: `["mahler", "status", "--project", "crate-cataloger", "--json"]` (hardcoded); `["mahler", "heartbeat", f"{project}#{number}", "--as", my_id]`, where `project`/`number` come from `mahler status --json` and `my_id` is the first 8 characters of `CLAUDE_CODE_SESSION_ID`. Each is a single argv element.
- `session_start.py`: `["mahler", "status", "--project", "crate-cataloger", "--json"]` (hardcoded); `my_id` is the first 8 characters of `CLAUDE_CODE_SESSION_ID`.
`scripts/apply-branch-protection.sh` uses a hardcoded repo, branch and payload and accepts only `--dry-run` and `-h`; any other argument exits 1.
Decision: no code change. Test: `NoShell`.

## 4. `serve` web surface — OK
- Per-run 128-bit key (`secrets.token_urlsafe(16)`) carried in the URL fragment, so it never reaches
  server logs or Referer headers; compared with `hmac.compare_digest` (`_authed`).
- Host-header allowlist against DNS rebinding: `host_ok`, now module-level and tested.
- `MAX_BODY` (4096) cap on POST bodies (checked in the serve handler).
- Response headers: CSP, `nosniff`, `no-referrer`, `no-store` (`H._send` in `cmd_serve`).
- The page never uses `innerHTML`; all dynamic content goes through `textContent`.
- Default bind is `0.0.0.0` over plain HTTP so a phone on the LAN can reach it. This is a documented
  product choice (README); `--host 127.0.0.1` restricts it. Left as is.
- Open item (not changed): no rate limiting on wrong-key attempts. A 128-bit key makes guessing
  infeasible.
Test: `HostOk`.

## 5. URL path construction from untrusted input — fixed
`resolve()` only returns ints. The gap was `undo`: `cmd_undo` passed
`release_id`, `folder` and `instance_id` from a user-supplied CSV straight into the DELETE path, so a
tampered `added-*.csv` (e.g. `folder` = `1/../../..`) could send an authenticated DELETE to an
unintended Discogs path. Now `Discogs.remove` and `Discogs.add` pass every id
through `_id()`, which requires a whole number (`>= 1`; folder `>= 0` for remove,
`>= 1` for add) and raises `ValueError` before any network call. `cmd_undo` reports such rows as
`FAILED <input>: <reason>` and continues. Tests: `Resolve`, `IdValidation`.

## 6. CSV formula injection — OK
`csv_safe` prefixes `=`, `+`, `-`, `@`, tab and CR with `'`. It is applied to every
Discogs-supplied or user-typed cell written to CSV: `AddLog.write`, `cmd_match` and `cmd_dupes`. Numeric ids and counts are not wrapped. Test:
`CsvSafe`.

## 7. Personal data in output files — OK
`added-*.csv`, `dupes-*.csv` and `*.review.csv` hold your collection and are git-ignored. They are
created by `AddLog`, `cmd_match` and `cmd_dupes`.

## 8. Dependency scan — OK
`crate.py` and `.claude/hooks/*.py` are stdlib-only: `grep -hE '^\s*(import|from) ' crate.py .claude/hooks/*.py` lists only standard-library modules, so no third-party import exists. There is no `requirements.txt` or `pyproject.toml`. The only third-party code is the GitHub Actions `actions/checkout` and `actions/setup-python`, covered by Dependabot (`.github/dependabot.yml`, weekly).

## 9. Denial lists / permission boundaries — OK
- The `serve` key and the Host check (`host_ok`; item 4), plus the token's own Discogs permissions.
- `.claude/hooks/block_generated_csvs.py` is a best-effort edit guard for generated CSVs, not a security boundary. Its Bash regex can be bypassed; that is accepted.
- `.claude/settings.json` `permissions.deny` blocks agent reads of `~/.config/crate-to-discogs/**` and `./.env` (item 11).
- The CI `permissions:` block (item 10).

## 10. CI workflow — fixed
`.github/workflows/ci.yml` had no `permissions:` block and so ran with the default `GITHUB_TOKEN` scope. It now has top-level `permissions: contents: read`. The trigger stays `pull_request` (never `pull_request_target`). Accepted risk: actions are pinned by major tag, not commit SHA, because Dependabot updates them weekly. Test: `CiPermissions`.

## 11. Agent settings — fixed
`.claude/settings.json` had hooks but no deny list. It now denies `Read(~/.config/crate-to-discogs/**)` and `Read(./.env)`; the hooks block is unchanged. No agent task needs the Discogs token, since `crate.py` reads it itself. Test: `AgentDenyList`.
