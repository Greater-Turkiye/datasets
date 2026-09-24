"""Give every source in the records a permanent copy, so a link that dies still reads.

    python tools/archive.py --dry-run          # what would be captured
    python tools/archive.py                    # fill in the missing archives
    python tools/archive.py --only data/events # one subtree

A source without an archive is a claim that rests on somebody else's uptime. ADR 0009 says we do not
copy third-party content: we summarise, link, and keep an archive link. This fills in that last part
for records written before the project had an archive account.

Two services are asked, the cheaper one first:

1. The Wayback availability API, which is free, needs no account and answers in one request. Most
   Wikipedia and Wikidata pages already have a capture, and an existing capture serves the purpose
   here: proving what the page said is secondary to the page still being readable at all.
2. Save Page Now, which does need an account, for pages nobody has captured yet.

Credentials come from the environment and are never written anywhere:

    IA_ACCESS_KEY, IA_SECRET_KEY   from https://archive.org/account/s3.php

Without them the script still runs; it takes what the availability API already has and reports what
it could not capture. The `captured_at` it records is the capture's own timestamp, not the time of
this run, because the date a reader needs is the date the copy was taken.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The repository's own loader, formatter and report; the path insert above makes it importable.
import gt

AVAILABILITY = "https://archive.org/wayback/available"
SAVE = "https://web.archive.org/save"
UA = "greater-turkiye-archiver (+https://github.com/Greater-Turkiye/datasets)"
POLL_S = 6  # a simple page takes ~15 s to capture; ask politely, not in a tight loop
POLL_TRIES = 25
PAUSE_S = 4  # between captures, so a long run does not look like a flood


def http(url: str, *, data: dict | None = None, auth: str | None = None, timeout: int = 120):
    body = urllib.parse.urlencode(data).encode() if data else None
    headers = {"Accept": "application/json", "User-Agent": UA}
    if body:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if auth:
        headers["Authorization"] = auth
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)


def stamp_to_iso(stamp: str) -> str:
    """`20260920025232` as the datetime the schema wants."""
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}Z"


def existing_capture(url: str) -> tuple[str, str] | None:
    """The newest capture the Wayback Machine already holds, if any."""
    status, body = http(f"{AVAILABILITY}?{urllib.parse.urlencode({'url': url})}", timeout=45)
    if status != 200:
        return None
    try:
        snap = json.loads(body).get("archived_snapshots", {}).get("closest") or {}
    except json.JSONDecodeError:
        return None
    if not snap.get("available") or not snap.get("url") or not snap.get("timestamp"):
        return None
    return snap["url"].replace("http://web.archive.org", "https://web.archive.org"), snap["timestamp"]


def save_now(url: str, auth: str) -> tuple[str, str] | None:
    """Ask Save Page Now for a capture and wait for it. Returns the archive URL and its timestamp."""
    status, body = http(
        SAVE, data={"url": url, "capture_outlinks": "0", "skip_first_archive": "1"}, auth=auth
    )
    if status != 200:
        print(f"    save refused: HTTP {status} {body[:120]}")
        return None
    try:
        job = json.loads(body).get("job_id")
    except json.JSONDecodeError:
        job = None
    if not job:
        print(f"    no job id: {body[:120]}")
        return None
    for _ in range(POLL_TRIES):
        time.sleep(POLL_S)
        status, body = http(f"{SAVE}/status/{job}", auth=auth)
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            continue
        if d.get("status") == "success" and d.get("timestamp"):
            return f"https://web.archive.org/web/{d['timestamp']}/{url}", d["timestamp"]
        if d.get("status") == "error":
            print(f"    save failed: {d.get('status_ext')} {str(d.get('message'))[:120]}")
            return None
    print("    save timed out")
    return None


STAMP_RE = re.compile(r"/web/(\d{14})/")


def save_anonymous(url: str) -> tuple[str, str] | None:
    """Save Page Now without an account: one GET that returns once the capture exists. Slower and more
    rate-limited than the authenticated API, which is why callers pass a small --limit."""
    req = urllib.request.Request(f"{SAVE}/{url}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            where = r.headers.get("Content-Location") or r.geturl()
    except urllib.error.HTTPError as e:
        print(f"    anonymous save refused: HTTP {e.code}")
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"    anonymous save failed: {e}")
        return None
    m = STAMP_RE.search(where or "")
    if not m:
        print(f"    anonymous save gave no capture: {str(where)[:120]}")
        return None
    return f"https://web.archive.org/web/{m.group(1)}/{url}", m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="list what is missing, capture nothing")
    ap.add_argument("--only", default="data", help="limit to a path under the repository")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many captures")
    ap.add_argument("--anonymous", action="store_true",
                    help="without IA keys, ask Save Page Now anonymously for pages nobody has captured")
    ap.add_argument("--tag", help="only records carrying this tag (e.g. otomatik)")
    ap.add_argument("--no-fail", action="store_true", help="exit 0 even when some sources stay unarchived")
    args = ap.parse_args()

    key, secret = os.environ.get("IA_ACCESS_KEY"), os.environ.get("IA_SECRET_KEY")
    auth = f"LOW {key}:{secret}" if key and secret else None
    if not auth and not args.dry_run:
        print("no IA_ACCESS_KEY/IA_SECRET_KEY: only captures that already exist will be used")

    report = gt.Report()
    records = gt.load_records(report)
    formatter = gt.Formatter(
        gt.load_schemas(), {rid: gt.record_label(rec) for rid, rec in records.items()}
    )
    only = (gt.ROOT / args.only).resolve()

    found = captured = failed = 0
    for _rid, rec in sorted(records.items(), key=lambda item: item[1].path):
        path = rec.path.resolve()
        if only != path and only not in path.parents:
            continue
        if args.tag and args.tag not in (rec.data.get("tags") or []):
            continue
        missing = [c for c in rec.data.get("sources", []) if c.get("url") and not c.get("archives")]
        if not missing:
            continue
        rel = rec.path.relative_to(gt.ROOT).as_posix()
        changed = False
        for citation in missing:
            url = citation["url"]
            found += 1
            print(f"  {rel}\n    {url}", flush=True)
            if args.dry_run:
                continue
            if args.limit and captured >= args.limit:
                print("    (limit reached)")
                break
            hit = existing_capture(url)
            how = "wayback"
            if not hit and auth:
                hit = save_now(url, auth)
                how = "saved"
            elif not hit and args.anonymous:
                hit = save_anonymous(url)
                how = "saved (anonymous)"
            if not hit:
                failed += 1
                print("    no archive")
                continue
            archive_url, stamp = hit
            citation["archives"] = [
                {"service": "wayback", "url": archive_url, "captured_at": stamp_to_iso(stamp)}
            ]
            captured += 1
            changed = True
            print(f"    {how} {stamp_to_iso(stamp)}", flush=True)
            time.sleep(PAUSE_S)
        if changed:
            kind = str(rec.data.get("schema", "")).partition("/")[0]
            text = rec.path.read_text(encoding="utf-8")
            rec.path.write_text(formatter.format(text, rec.data, kind), encoding="utf-8", newline="\n")

    if args.dry_run:
        print(f"{found} sources without an archive")
        return 0
    print(f"{found} sources without an archive; {captured} archived, {failed} still without one")
    return 1 if failed and not args.no_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
