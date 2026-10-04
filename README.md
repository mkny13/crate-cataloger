# crate.py: type titles, fill your Discogs collection

Commands: `setup`, `quick`, `match`, `add`, `undo`, `serve`, `dupes`. Run `python3 crate.py -h` for usage.

Plain Python 3, no packages to install, no LLM. Matches each "Artist - Title" against
Discogs and adds the **main release** of the master (you said exact pressing doesn't matter).

## One-time setup (Mac)

1. Put `crate.py` in a folder, e.g. `~/crate`.
2. Open Terminal there and run `python3 crate.py setup`.
   It sends you to https://www.discogs.com/settings/developers → "Generate new token"; paste it in.
   (macOS may offer to install the developer tools the first time you run `python3`. Say yes.)

## Two ways to use it

**Quick mode: fastest when you're sitting at the crate**

    python3 crate.py quick

Type `radiohead - ok computer`, press Enter to see matches, then Enter again to add #1.
Or type `2`–`4` for another match, `s` to skip, or paste a Discogs URL.
Records you already own are flagged. A blank line quits. That's about two keystrokes per record after typing.

**Batch mode: type the list anywhere (phone notes, etc.), process later**

    python3 crate.py match crate1.txt        # writes crate1.review.csv
    # open the CSV, fix the 'pick' column on rows marked CHECK / NOT FOUND
    python3 crate.py add crate1.review.csv   # shows the plan, asks y/N, then adds

`pick` accepts `1`–`4` (which candidate column), an id like `m21491` / `r83182`, or a Discogs URL.
Blank = skip. Rows are pre-picked only when the match is clear. Ambiguous ones
(self-titled albums, multiple albums with the same name) are marked `CHECK` and left blank.

**From your phone (Android/iPhone on the same Wi-Fi), no Termux needed**

    python3 crate.py serve

Prints a URL like `http://192.168.1.x:8765`. Open it on your phone, type `Artist - Title`, tap a match to add it.
Owned records are marked. The printed link ends in `#k=<key>`, a random key made fresh on each run; the server
refuses requests without it, so open the full link (the key is never sent in the URL path or logged).
It still speaks plain HTTP, so only run it on a network you trust. Use `--host 127.0.0.1` to serve this computer only.
Tick "Show covers" for thumbnails (off by default; remembered per browser). Each added record appears in a list with an **Undo** button that removes it from your collection.
The log (`added-<timestamp>.csv`) is written when you stop it with Ctrl-C.

**Duplicates:** `python3 crate.py dupes` lists albums you own more than once and writes `dupes-<timestamp>.csv`.

**Undo:** every add run writes `added-<timestamp>.csv`. Run `python3 crate.py undo added-....csv` to remove those items.

## Narrowing with details

Add hints after a `|` when a title is ambiguous: label, catalog number, barcode, year, or a format like `180 gram`.

    radiohead - ok computer | 180 gram
    radiohead - ok computer | xl 7 | 2016

The script searches releases matching those hints, then resolves each to its master (candidates show the matched
label / catalog no. / format in brackets). If nothing matches the hints, it falls back to a normal search.
Works in `quick`, `serve`, and batch lists.

## Typo correction

If Discogs finds nothing (or only a weak match), the script asks Apple's iTunes search, which tolerates
misspellings, for the closest album, then searches Discogs for that. Corrected matches are marked `✎`, e.g.
`magione - feels so good` finds `✎ ... Chuck Mangione - Feels So Good`. If iTunes is unreachable, it's skipped silently.
Note: this sends the text you typed to Apple's public search API.

## Notes

- New items go to the Uncategorized folder. Use `--folder N` on `quick`/`add` to change that.
- Discogs allows 60 API calls/minute. The script slows itself down. Expect about 20–30 records/minute in batch mode.
- Android: install Termux (from F-Droid), `pkg install python`, copy `crate.py` over, same commands.
- Tips for matching: `Artist - Title` beats a bare title. For "Various Artists" comps, use `Various - Title`.

## Privacy

Your token is stored in `~/.config/crate-to-discogs/token` (mode 600) or read from `DISCOGS_TOKEN`; it is only sent to
`api.discogs.com`. The `added-*.csv` and `dupes-*.csv` files list your collection, so they are git-ignored. Don't commit them.
