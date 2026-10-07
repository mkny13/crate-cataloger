#!/usr/bin/env python3
"""
crate.py - add records to your Discogs collection by typing titles.

No third-party packages: plain Python 3.8+ (macOS, Linux, Android/Termux).

Commands
  setup                 save your Discogs personal access token
  quick                 type a title, press Enter to accept the match, it's added immediately
  match LIST.txt        batch: match every line, write LIST.review.csv for you to check
  add REVIEW.csv        add every row that has something in the "pick" column
  undo LOG.csv          remove everything a previous quick/add run added
  serve                 web UI for quick mode, reachable from a phone on your LAN (key-protected)
  dupes                 list albums you own more than once, write dupes-<timestamp>.csv

Input lines look like "Artist - Title" (any dash works). A bare title also
works, but it's less accurate. Blank lines and lines starting with # are skipped.
"""
import argparse
import csv
import difflib
import getpass
import hmac
import json
import os
import secrets
import socket
import threading
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API = "https://api.discogs.com"
UA = "CrateToDiscogs/1.0 +https://github.com/mkny13/crate-cataloger"
TOKEN_FILE = Path.home() / ".config" / "crate-to-discogs" / "token"
DEFAULT_FOLDER = 1  # 1 = "Uncategorized"; folder 0 ("All") can't be added to
N_CANDIDATES = 4
FIX_BELOW = 0.55    # below this top score (or no results), try typo correction
CONFIDENT = 0.80    # similarity needed for an automatic pick in batch mode
MARGIN = 0.05       # ...and it must beat the runner-up by this much
MAX_BODY = 4096     # largest request body `serve` will read


# ---------------------------------------------------------------- API client

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would resend the Authorization header to the new host."""
    def redirect_request(self, *a, **kw):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _id(value, name, minimum=1):
    """Ids go into URL paths, so only plain integers (>= minimum) may through."""
    text = str(value).strip()
    # int() would also take "1_0", "+5" and non-ASCII digits, so check the shape first.
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    n = int(text)
    if n < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {n}")
    return n


class Discogs:
    def __init__(self, token):
        self._auth = "Discogs token" "=" + token  # Discogs' Authorization scheme
        self._user = None

    def call(self, method, path, params=None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            req = urllib.request.Request(
                url,
                method=method,
                data=b"" if method in ("POST", "PUT") else None,
                headers={"User-Agent": UA,
                         "Authorization": self._auth},
            )
            try:
                with _opener.open(req, timeout=30) as resp:
                    self._pace(resp.headers)
                    body = resp.read()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(15 * (attempt + 1))
                    continue
                if e.code == 404:
                    return None
                detail = e.read()[:300].decode("utf-8", "replace")
                if e.code == 401:
                    sys.exit("Discogs rejected the token (401). Run: python3 crate.py setup")
                raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}")
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise RuntimeError(f"Network error: {e.reason}")
        raise RuntimeError("Still rate-limited after several retries; try again in a minute.")

    @staticmethod
    def _pace(headers):
        # 60 requests/min window. Slow down before we hit the wall.
        rem = headers.get("X-Discogs-Ratelimit-Remaining")
        if rem is not None and rem.isdigit() and int(rem) <= 3:
            time.sleep(20)

    @property
    def username(self):
        if not self._user:
            self._user = self.call("GET", "/oauth/identity")["username"]
        return self._user

    def search(self, kind, **params):
        params = {k: v for k, v in params.items() if v}
        res = self.call("GET", "/database/search", {"type": kind, "per_page": 10, **params})
        return (res or {}).get("results", [])

    def main_release(self, master_id):
        m = self.call("GET", f"/masters/{master_id}")
        if not m or not m.get("main_release"):
            raise RuntimeError(f"Master {master_id} not found or has no main release")
        return m["main_release"]

    def collection_index(self):
        """Sets of release ids and master ids already in the collection."""
        releases, masters, page = set(), set(), 1
        while True:
            res = self.call("GET", f"/users/{self.username}/collection/folders/0/releases",
                            {"per_page": 100, "page": page})
            if not res:
                break
            for item in res.get("releases", []):
                bi = item.get("basic_information", {})
                releases.add(item.get("id") or bi.get("id"))
                if bi.get("master_id"):
                    masters.add(bi["master_id"])
            pages = res.get("pagination", {}).get("pages", 1)
            if page >= pages:
                break
            page += 1
        return releases, masters

    def collection_items(self):
        """Every collection instance (a release owned twice appears twice)."""
        items, page = [], 1
        while True:
            res = self.call("GET", f"/users/{self.username}/collection/folders/0/releases",
                            {"per_page": 100, "page": page})
            if not res:
                break
            items += res.get("releases", [])
            if page >= res.get("pagination", {}).get("pages", 1):
                break
            page += 1
        return items

    def add(self, release_id, folder):
        release_id, folder = _id(release_id, "release_id"), _id(folder, "folder", minimum=1)
        res = self.call("POST", f"/users/{self.username}/collection/folders/{folder}/releases/{release_id}")
        return (res or {}).get("instance_id")

    def remove(self, release_id, folder, instance_id):
        release_id, instance_id = _id(release_id, "release_id"), _id(instance_id, "instance_id")
        folder = _id(folder, "folder", minimum=0)
        self.call("DELETE", f"/users/{self.username}/collection/folders/{folder}"
                            f"/releases/{release_id}/instances/{instance_id}")


# ---------------------------------------------------------------- matching

def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\(\d+\)", "", s)          # Discogs artist disambiguators: "Nirvana (2)"
    s = s.replace("*", "").replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\bthe\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_line(line):
    parts = re.split(r"\s+[-–—]\s+", line.strip(), maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, line.strip())


def similarity(query, result_title):
    return difflib.SequenceMatcher(None, norm(query), norm(result_title)).ratio()


def find_hinted(dc, base, hints):
    """Search releases matching the hints (label, catalog number, '180 gram', year...),
    then resolve each hit to its master. Returns [] if nothing matches."""
    artist, title = split_line(base)
    params = {"q": " ".join(hints)}
    if artist:
        params.update(artist=artist, release_title=title)
    else:
        params["q"] = base + " " + params["q"]
    cands, seen = [], set()
    for rank, r in enumerate(dc.search("release", **params)):
        master = r.get("master_id") or None
        key = master or r["id"]
        if key in seen:
            continue
        seen.add(key)
        score = similarity(base, r.get("title", ""))
        detail = " / ".join(x for x in [", ".join(r.get("label", [])[:1]), r.get("catno", ""),
                                        ", ".join(r.get("format", [])[:3])] if x)
        cands.append({
            "ref": ("m" + str(master)) if master else "r" + str(r["id"]),
            "title": r.get("title", "?") + (f" [{detail}]" if detail else ""),
            "year": r.get("year") or "",
            "score": score,
            "sort": score - 0.01 * rank,
            "master_id": master,
            "release_id": None if master else r["id"],
            "thumb": r.get("thumb") or "",
        })
    cands.sort(key=lambda c: c["sort"], reverse=True)
    return cands[:N_CANDIDATES]


def find(dc, line):
    """Return up to N_CANDIDATES candidates, best first.
    Each candidate: {"ref": "m123"|"r456", "title", "year", "score", "master_id", "release_id"}
    'Artist - Title | hint | hint' narrows by label / catalog no. / format / year."""
    base, *hints = [p.strip() for p in line.split("|")]
    hints = [h for h in hints if h]
    if hints:
        cands = find_hinted(dc, base, hints)
        if cands:
            return cands
    cands = _search(dc, base)
    if cands and cands[0]["score"] >= FIX_BELOW:
        return cands
    fixed = spell_fix(base)
    if fixed and norm(fixed) != norm(base):
        fcands = _search(dc, fixed)
        for c in fcands:
            c["fixed"] = fixed
        seen = {c["master_id"] or c["ref"] for c in fcands}
        cands = fcands + [c for c in cands if (c["master_id"] or c["ref"]) not in seen]
    return cands[:N_CANDIDATES]


def spell_fix(line):
    """Typo correction: Discogs search is exact-ish, so ask iTunes' typo-tolerant search for the
    closest album and return it as 'Artist - Title'. None if unavailable or no hit."""
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": line.replace(" - ", " "), "entity": "album", "limit": 1})
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            hit = json.loads(resp.read())["results"][0]
        title = re.sub(r"\s+-\s+(Single|EP)$", "", hit["collectionName"])
        return f"{hit['artistName']} - {title}"
    except (OSError, ValueError, KeyError, IndexError):
        return None


def _search(dc, line):
    artist, title = split_line(line)
    attempts = []
    for kind in ("master", "release"):
        if artist:
            attempts.append((kind, {"artist": artist, "release_title": title}))
        attempts.append((kind, {"q": line.replace(" - ", " ")}))

    results, kind = [], None
    for kind, params in attempts:
        results = dc.search(kind, **params)
        if results:
            break
    if not results:
        return []

    cands = []
    for rank, r in enumerate(results):
        is_master = kind == "master"
        score = similarity(line, r.get("title", ""))
        # Small tiebreaks: Discogs' own relevance order, and (for releases) popularity
        tiebreak = -0.01 * rank
        if not is_master:
            tiebreak += min(r.get("community", {}).get("have", 0), 5000) / 1_000_000
        cands.append({
            "ref": ("m" if is_master else "r") + str(r["id"]),
            "title": r.get("title", "?"),
            "year": r.get("year") or "",
            "score": score,
            "sort": score + tiebreak,
            "master_id": r["id"] if is_master else (r.get("master_id") or None),
            "release_id": None if is_master else r["id"],
            "thumb": r.get("thumb") or "",
        })
    cands.sort(key=lambda c: c["sort"], reverse=True)
    # De-dupe releases that share a master (keeps the best-ranked one)
    seen, out = set(), []
    for c in cands:
        key = c["master_id"] or c["ref"]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out[:N_CANDIDATES]


def is_confident(cands):
    if not cands or cands[0]["score"] < CONFIDENT:
        return False
    return len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= MARGIN


def csv_safe(v):
    """Stop spreadsheets from running Discogs-supplied text as a formula."""
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def label(c):
    yr = f" ({c['year']})" if c["year"] else ""
    fix = "✎ " if c.get("fixed") else ""
    return f"{fix}{c['ref']} · {c['title']}{yr}"


def owned(c, coll):
    rels, masters = coll
    return (c["master_id"] and c["master_id"] in masters) or (c["release_id"] and c["release_id"] in rels)


def resolve(dc, ref):
    """'m123' / 'r456' / a Discogs URL / a bare number (treated as a release) -> release id."""
    ref = ref.strip()
    m = re.search(r"/(master|release)/(\d+)", ref)
    if m:
        ref = ("m" if m.group(1) == "master" else "r") + m.group(2)
    if ref.lower().startswith("m") and ref[1:].isdigit():
        return dc.main_release(int(ref[1:]))
    if ref.lower().startswith("r") and ref[1:].isdigit():
        return int(ref[1:])
    if ref.isdigit():
        return int(ref)
    raise ValueError(f"Don't understand pick '{ref}'")


# ---------------------------------------------------------------- logging (for undo)

class AddLog:
    def __init__(self, folder):
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        self.path, n = Path(f"added-{stamp}.csv"), 1
        while self.path.exists():
            n += 1
            self.path = Path(f"added-{stamp}-{n}.csv")
        self.folder = folder
        self._f = None

    def write(self, release_id, instance_id, text):
        if self._f is None:
            self._f = open(self.path, "w", newline="", encoding="utf-8")
            self._w = csv.writer(self._f)
            self._w.writerow(["release_id", "instance_id", "folder", "input"])
        self._w.writerow([release_id, instance_id, self.folder, csv_safe(text)])
        self._f.flush()

    def close(self):
        if self._f:
            self._f.close()
            print(f"\nLog written to {self.path} (use it with 'undo' if needed)")


# ---------------------------------------------------------------- commands

def cmd_setup(_args):
    print("Create a token at https://www.discogs.com/settings/developers ('Generate new token').")
    tok = getpass.getpass("Paste token (input hidden): ").strip()
    if not tok:
        sys.exit("No token entered.")
    user = Discogs(tok).username
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    TOKEN_FILE.chmod(0o600)  # also tightens a file left over from an older version
    print(f"OK - authenticated as {user}. Token saved to {TOKEN_FILE}")


def client():
    tok = os.environ.get("DISCOGS_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    if not tok:
        sys.exit("No token yet. Run: python3 crate.py setup")
    return Discogs(tok)


def cmd_quick(args):
    dc = client()
    print(f"Discogs user: {dc.username}. Loading your collection for duplicate checks...")
    coll = dc.collection_index()
    print(f"{len(coll[0])} releases in collection. Adding to folder {args.folder}.")
    print("Type 'Artist - Title'. At the prompt: Enter = #1, 2-4 = that one, "
          "s = skip, or paste a Discogs URL. Blank line or Ctrl-D to quit.\n")
    log = AddLog(args.folder)
    try:
        while True:
            try:
                line = input("record> ").strip()
            except EOFError:
                break
            if not line:
                break
            cands = find(dc, line)
            if not cands:
                print("  no match. Try different spelling, or paste a Discogs URL as the line.\n")
                continue
            for i, c in enumerate(cands, 1):
                flag = "  [ALREADY OWNED]" if owned(c, coll) else ""
                print(f"  {i}. {label(c)}{flag}")
            choice = input("  pick [1]: ").strip().lower()
            if choice == "s":
                print("  skipped\n")
                continue
            try:
                if choice in ("", "1", "2", "3", "4"):
                    idx = int(choice or 1) - 1
                    if idx >= len(cands):
                        print("  no such option, skipped\n")
                        continue
                    chosen = cands[idx]
                    if owned(chosen, coll) and input("  Already owned. Add a second copy? [y/N] ").strip().lower() != "y":
                        print("  skipped\n")
                        continue
                    rid = chosen["release_id"] or dc.main_release(chosen["master_id"])
                    shown = label(chosen)
                else:
                    rid = resolve(dc, choice)
                    shown = choice
                inst = dc.add(rid, args.folder)
                log.write(rid, inst, line)
                coll[0].add(rid)
                if choice in ("", "1", "2", "3", "4") and chosen["master_id"]:
                    coll[1].add(chosen["master_id"])
                print(f"  added: {shown}\n")
            except (RuntimeError, ValueError) as e:
                print(f"  error: {e}\n")
    finally:
        log.close()


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Crate</title><style>
body{font:18px system-ui;margin:0;padding:12px;background:#111;color:#eee}
input,button{font:inherit;padding:14px;border-radius:10px;border:0;width:100%;box-sizing:border-box}
input{background:#222;color:#fff;margin-bottom:8px}
button{background:#2a6;color:#fff;margin:4px 0;text-align:left}
button.own{background:#a52}button.skip{background:#444}
#log div{padding:6px 0;border-bottom:1px solid #333;font-size:15px;display:flex;gap:8px;align-items:center}
#log span{flex:1}#log button{width:auto;margin:0;padding:6px 12px;background:#a52}
button.cand{display:flex;align-items:center;gap:12px}
button.cand img{width:64px;height:64px;object-fit:cover;border-radius:6px;background:#333;flex:none}
label.t{display:block;margin:0 0 8px;font-size:15px;color:#aaa}label.t input{width:auto;margin:0 6px 0 0}
</style></head><body>
<label class=t><input type=checkbox id=cov>Show covers (slower)</label>
<form id=f><input id=q placeholder="Artist - Title" autocomplete=off autofocus></form>
<div id=c></div><div id=log></div>
<script>
const q=document.getElementById('q'),c=document.getElementById('c'),log=document.getElementById('log');
let line='';
const H={'X-Crate-Key':new URLSearchParams(location.hash.slice(1)).get('k')||''};
const cov=document.getElementById('cov');
try{cov.checked=localStorage.covers==='1'}catch(e){}
cov.onchange=()=>{try{localStorage.covers=cov.checked?'1':'0'}catch(e){}};
function note(t,id){const d=document.createElement('div'),s=document.createElement('span');s.textContent=t;d.appendChild(s);
  if(id){const u=document.createElement('button');u.textContent='Undo';
    u.onclick=async()=>{u.disabled=true;const r=await fetch('/api/undo',{method:'POST',headers:H,body:JSON.stringify({id})});
      const j=await r.json();if(j.error){u.disabled=false;s.textContent='ERROR: '+j.error}
      else{s.textContent='Removed: '+t.replace('Added: ','');u.remove()}}
    d.appendChild(u)}
  log.prepend(d)}
document.getElementById('f').onsubmit=async e=>{
  e.preventDefault();line=q.value.trim();if(!line)return;c.textContent='Searching...';
  const r=await fetch('/api/find?q='+encodeURIComponent(line)+(cov.checked?'&thumbs=1':''),{headers:H});const j=await r.json();
  c.textContent='';
  if(j.error){c.textContent=j.error;return}
  if(!j.length)c.textContent='No match. Try different spelling or paste a Discogs URL.';
  j.forEach(x=>{const b=document.createElement('button');
    b.className='cand'+(x.owned?' own':'');
    if(x.thumb){const i=document.createElement('img');i.loading='lazy';i.src=x.thumb;b.appendChild(i)}
    const t=document.createElement('span');t.textContent=(x.owned?'⚠ ALREADY OWNED · ':'')+x.label;b.appendChild(t);
    b.onclick=()=>{if(x.owned&&!confirm('You already own this. Add a second copy?'))return;add(x.ref,x.label)};c.appendChild(b)});
  const s=document.createElement('button');s.className='skip';s.textContent='Skip';
  s.onclick=()=>{c.textContent='';q.value='';q.focus()};c.appendChild(s);
};
async function add(ref,shown){
  c.textContent='Adding...';
  const r=await fetch('/api/add',{method:'POST',headers:H,body:JSON.stringify({line,ref})});
  const j=await r.json();note(j.error?('ERROR: '+j.error):('Added: '+shown),j.error?null:j.id);
  c.textContent='';q.value='';q.focus();
}
</script></body></html>"""


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def host_ok(host, ip, port):
    # Reject DNS-rebinding: only accept the Host header values we told the user to open.
    name = (host or "").rsplit(":", 1)[0].strip("[]")
    return name in ("localhost", "127.0.0.1", ip) and (host or "").rsplit(":", 1)[-1] == str(port)


def cmd_serve(args):
    dc = client()
    print(f"Discogs user: {dc.username}. Loading collection for duplicate checks...")
    coll = dc.collection_index()
    log = AddLog(args.folder)
    added = {}  # instance_id -> (release_id, master_id), for the phone's Undo button
    lock = threading.Lock()  # one Discogs call sequence at a time keeps rate-limit pacing sane
    key = secrets.token_urlsafe(16)  # per-run secret; the URL fragment carries it, so it never hits logs

    ip = lan_ip()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "img-src https://*.discogs.com; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            return host_ok(self.headers.get("Host"), ip, args.port) and hmac.compare_digest(
                self.headers.get("X-Crate-Key", ""), key)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                if not host_ok(self.headers.get("Host"), ip, args.port):
                    return self._send(403, "{}")
                return self._send(200, PAGE, "text/html")
            if u.path == "/api/find":
                if not self._authed():
                    return self._send(403, "{}")
                qs = urllib.parse.parse_qs(u.query)
                line = qs.get("q", [""])[0].strip()
                thumbs = qs.get("thumbs") == ["1"]
                try:
                    with lock:
                        cands = find(dc, line)
                    out = [{"ref": c["ref"], "label": label(c), "owned": bool(owned(c, coll)),
                            "thumb": c["thumb"] if thumbs else ""} for c in cands]
                except RuntimeError as e:
                    out = {"error": str(e)}
                return self._send(200, json.dumps(out))
            self._send(404, "{}")

        def do_POST(self):
            if self.path not in ("/api/add", "/api/undo"):
                return self._send(404, "{}")
            if not self._authed():
                return self._send(403, "{}")
            try:
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= MAX_BODY:
                    return self._send(413, "{}")
                body = json.loads(self.rfile.read(n))
                if self.path == "/api/undo":
                    with lock:
                        rid, master = added.pop(body["id"])
                        dc.remove(rid, args.folder, body["id"])
                        coll[0].discard(rid)
                        coll[1].discard(master)
                    return self._send(200, json.dumps({"ok": True}))
                with lock:
                    rid = resolve(dc, body["ref"])
                    inst = dc.add(rid, args.folder)
                    log.write(rid, inst, body.get("line", ""))
                    coll[0].add(rid)
                    master = None
                    if body["ref"][:1] == "m" and body["ref"][1:].isdigit():
                        master = int(body["ref"][1:])
                        coll[1].add(master)
                    added[inst] = (rid, master)
                self._send(200, json.dumps({"ok": True, "id": inst}))
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                self._send(200, json.dumps({"error": str(e)}))

        def log_message(self, *a):
            pass

    print(f"Open on your phone (same Wi-Fi): http://{ip}:{args.port}/#k={key}   (Ctrl-C to stop)")
    print("The link contains a one-time key; anyone without it is refused.")
    try:
        ThreadingHTTPServer((args.host, args.port), H).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.close()


def cmd_match(args):
    dc = client()
    src = Path(args.list)
    lines = [l.strip() for l in src.read_text(encoding="utf-8").splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    print(f"Discogs user: {dc.username}. Loading collection for duplicate checks...")
    coll = dc.collection_index()
    out = src.with_suffix(".review.csv")
    counts = {"ok": 0, "CHECK": 0, "NOT FOUND": 0, "OWNED": 0}
    picked_masters = set()
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["input", "status", "pick", "c1", "c2", "c3", "c4"])
        for n, line in enumerate(lines, 1):
            try:
                cands = find(dc, line)
            except RuntimeError as e:
                cands, err = [], str(e)
            else:
                err = ""
            if not cands:
                status, pick = "NOT FOUND", ""
                if err:
                    status += f" ({err[:60]})"
            elif owned(cands[0], coll) or (cands[0]["master_id"] and cands[0]["master_id"] in picked_masters):
                status, pick = "OWNED", ""
            elif is_confident(cands):
                status, pick = "ok", "1"
                if cands[0]["master_id"]:
                    picked_masters.add(cands[0]["master_id"])
            else:
                status, pick = "CHECK", ""
            counts[status.split(" (")[0]] += 1
            row = [csv_safe(line), status, pick] + [csv_safe(label(c)) for c in cands]
            w.writerow(row + [""] * (7 - len(row)))
            top = label(cands[0]) if cands else "-"
            print(f"[{n}/{len(lines)}] {status:9} {line}  ->  {top}")
    print(f"\n{counts}\nWrote {out}")
    print("Open it, fill the 'pick' column (1-4, an m/r id, or a Discogs URL; blank = skip),")
    print(f"then run:  python3 crate.py add {out.name}")


def cmd_add(args):
    dc = client()
    with open(args.review, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("pick") or "").strip()]
    if not rows:
        sys.exit("Nothing has a pick - nothing to add.")
    plan = []
    for r in rows:
        p = r["pick"].strip()
        if p in ("1", "2", "3", "4"):
            cell = r.get(f"c{p}", "")
            if not cell:
                print(f"  skip (no candidate {p}): {r['input']}")
                continue
            plan.append((r["input"], cell.split(" · ")[0], cell))
        else:
            plan.append((r["input"], p, p))
    for inp, _, shown in plan:
        print(f"  {inp:40.40}  ->  {shown}")
    if not args.yes and input(f"\nAdd these {len(plan)} to folder {args.folder}? [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled.")
    log = AddLog(args.folder)
    ok = 0
    try:
        for inp, ref, _ in plan:
            try:
                rid = resolve(dc, ref)
                inst = dc.add(rid, args.folder)
                log.write(rid, inst, inp)
                ok += 1
                print(f"  added  {inp}")
            except (RuntimeError, ValueError) as e:
                print(f"  FAILED {inp}: {e}")
    finally:
        log.close()
    print(f"{ok}/{len(plan)} added.")


def cmd_undo(args):
    dc = client()
    with open(args.log, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if input(f"Remove {len(rows)} items listed in {args.log} from your collection? [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled.")
    for r in rows:
        if not r["instance_id"]:
            print(f"  no instance id, skip: {r['input']}")
            continue
        try:
            dc.remove(r["release_id"], r["folder"], r["instance_id"])
            print(f"  removed {r['input']}")
        except (RuntimeError, ValueError) as e:
            print(f"  FAILED {r['input']}: {e}")


def cmd_dupes(_args):
    dc = client()
    print(f"Discogs user: {dc.username}. Loading collection...")
    items = dc.collection_items()
    groups = {}
    for it in items:
        bi = it.get("basic_information", {})
        key = ("m", bi["master_id"]) if bi.get("master_id") else ("r", it.get("id") or bi.get("id"))
        groups.setdefault(key, []).append(it)
    dupes = [g for g in groups.values() if len(g) > 1]
    dupes.sort(key=lambda g: (g[0]["basic_information"]["artists"][0]["name"].lower(),
                              g[0]["basic_information"]["title"].lower()))
    out = Path(f"dupes-{datetime.now():%Y%m%d-%H%M%S}.csv")
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["artist", "title", "copies", "same_release", "release_id", "instance_id",
                    "folder_id", "year", "label", "format", "url"])
        for g in dupes:
            rids = {it["id"] for it in g}
            for it in g:
                bi = it["basic_information"]
                print_artist = ", ".join(a["name"] for a in bi["artists"])
                fmt = " ".join(filter(None, [bi["formats"][0]["name"] if bi.get("formats") else "",
                                             ", ".join(bi["formats"][0].get("descriptions", [])) if bi.get("formats") else ""]))
                w.writerow([csv_safe(print_artist), csv_safe(bi["title"]), len(g),
                            "yes" if len(rids) == 1 else "no", it["id"], it.get("instance_id"), it.get("folder_id"), bi.get("year"),
                            csv_safe("; ".join(l["name"] for l in bi.get("labels", []))), csv_safe(fmt),
                            f"https://www.discogs.com/release/{it['id']}"])
            first = g[0]["basic_information"]
            kind = "same release x%d" % len(g) if len(rids) == 1 else "%d different pressings" % len(g)
            print(f"  {', '.join(a['name'] for a in first['artists'])} - {first['title']}  ({kind})")
    print(f"\n{len(items)} items, {len(dupes)} duplicated albums. Details: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    q = sub.add_parser("quick"); q.add_argument("--folder", type=int, default=DEFAULT_FOLDER); q.set_defaults(fn=cmd_quick)
    m = sub.add_parser("match"); m.add_argument("list"); m.set_defaults(fn=cmd_match)
    a = sub.add_parser("add"); a.add_argument("review"); a.add_argument("--folder", type=int, default=DEFAULT_FOLDER)
    a.add_argument("-y", "--yes", action="store_true"); a.set_defaults(fn=cmd_add)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--host", default="0.0.0.0", help="bind address (127.0.0.1 = this computer only)")
    sv.add_argument("--folder", type=int, default=DEFAULT_FOLDER)
    sv.set_defaults(fn=cmd_serve)
    sub.add_parser("dupes").set_defaults(fn=cmd_dupes)
    u = sub.add_parser("undo"); u.add_argument("log"); u.set_defaults(fn=cmd_undo)
    args = ap.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
