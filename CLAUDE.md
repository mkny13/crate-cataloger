# crate-cataloger

Single-script, standard-library-only Python tool (`crate.py`) that adds records to a Discogs collection from typed titles. See `AGENTS.md` for verify commands and the Mahler workflow.

## Run

`python3 crate.py -h` lists commands: `setup`, `quick`, `match LIST.txt`, `add REVIEW.csv`, `undo LOG.csv`, `serve`, `dupes`.

## Generated CSVs (never edit by hand)

- `added-<timestamp>.csv`: log of a `quick`/`add` run; `undo` reads it.
- `dupes-<timestamp>.csv`: output of `dupes`.
- `LIST.review.csv`: output of `match`; the only one you fill in (the `pick` column) before `add`.

These are gitignored personal data. A hook (`.claude/hooks/block_generated_csvs.py`) blocks edits to `added-*.csv` and `dupes-*.csv`.
