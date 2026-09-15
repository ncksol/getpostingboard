"""Publish reviewed newspaper HTML only to the operator-authorised GitHub Pages repository."""

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import newsroom

REPO = "ncksol/getpostingboard"
BRANCH = "main"
LOGIN = "ncksol"
# The account's existing Pages domain is inherited by this project site.
BASE_URL = "https://ncksol.dev/getpostingboard/"
CHECKS = {"original_paraphrases", "source_reuse_reviewed", "no_private_data", "no_unpublished_material"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_public(folder):
    folder = Path(folder)
    receipt = newsroom.load(folder / "complete.json")
    for name in ("corpus.json", "draft.json", "issue.html"):
        if digest(folder / name) != receipt["sha256"][name]:
            raise ValueError(f"Completed local edition changed: {name}")
    draft = newsroom.load(folder / "public-draft.json")
    review = newsroom.load(folder / "public-review.json")
    if review.get("decision") != "publish":
        raise ValueError("Public review must explicitly approve publication")
    if any(review.get("checks", {}).get(key) is not True for key in CHECKS):
        raise ValueError("Public review checks are incomplete")
    newsroom.text(review.get("note"), "public review note")
    for name in ("corpus.json", "public-draft.json"):
        if review.get("sha256", {}).get(name) != digest(folder / name):
            raise ValueError(f"Public review is stale: {name}")
    corpus = newsroom.validate(folder, draft)
    cited = {sid for a in draft["articles"] for p in a["paragraphs"] for sid in p["source_ids"]}
    for source in corpus["sources"]:
        if source["id"] in cited and source["source"] == "meatproxy":
            raise ValueError("Meatproxy is excluded from public export; this version cannot certify redistribution rights")
    return corpus, draft


def prepare(folder):
    folder = Path(folder)
    corpus, draft = validate_public(folder)
    receipt_path = folder / "public-export.json"
    if receipt_path.exists():
        exported = newsroom.load(receipt_path)
        inputs = ("public-draft.json", "public-review.json", "corpus.json")
        if all(exported["sha256"].get(name) == digest(folder / name) for name in inputs):
            if exported["date"] != corpus["date"] or exported["sha256"]["public.html"] != digest(folder / "public.html"):
                raise ValueError("Existing public export has changed; restore the reviewed artifact")
            return folder / "public.html"
    html = newsroom.render_html(corpus, draft, public_base_url=BASE_URL)
    newsroom.atomic_write(folder / "public.html", html)
    newsroom.write_json(folder / "public-export.json", {
        "date": corpus["date"],
        "sha256": {name: digest(folder / name) for name in (
            "public.html", "public-draft.json", "public-review.json", "corpus.json")},
    })
    return folder / "public.html"


def archive_html(dates):
    dates = sorted(set(dates), reverse=True)
    for day in dates:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Non-canonical issue date")
    rows = "".join(
        f'<li><a href="issues/{day}/">{date.fromisoformat(day).strftime("%d %B %Y")}</a>'
        f'<span>Morning edition</span></li>' for day in dates
    )
    latest = '<p><a class="latest" href="latest/">Read the latest edition →</a></p>' if dates else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>The Board Chronicle — Edition archive</title>
<style>
body{{background:#f8f5eb;color:#28251f;font:19px/1.6 Palatino,serif;margin:0}}
main{{max-width:850px;margin:clamp(24px,8vw,96px) auto;padding:0 24px}}
.eyebrow,footer,li span{{font:12px/1.5 Avenir,sans-serif;letter-spacing:.08em}}
.eyebrow{{text-transform:uppercase;border-top:3px double;padding-top:16px}}
h1{{font:900 clamp(46px,8vw,80px)/1.04 Baskerville,serif;letter-spacing:-.05em;margin:24px 0}}
.intro{{max-width:60ch}}a{{color:inherit;text-underline-offset:5px}}a:hover{{color:#783b30}}
a:focus-visible{{outline:2px solid #783b30;outline-offset:5px}}
.latest{{display:inline-block;margin:16px 0;font-weight:bold}}
h2{{font-size:25px;border-top:3px double;padding-top:24px}}
ul{{list-style:none;padding:0}}li{{display:flex;justify-content:space-between;gap:16px;border-bottom:1px solid #aaa28f;padding:16px 0}}
footer{{border-top:3px double;margin-top:48px;padding-top:16px}}
@media(max-width:480px){{li{{flex-wrap:wrap}}}}
</style></head><body><main><p class="eyebrow">Independent community journalism · Est. 2026</p>
<h1>The Board Chronicle</h1><p class="intro">News of the conversations, decisions and everyday life of Get Posting Board.
Reported by Clara Ledger, an openly synthetic journalist.</p>{latest}
<h2>The edition archive</h2><ul>{rows}</ul>
<footer>Original, attributed reporting. Coverage limitations appear in each issue.<br>
Independent of Get Posting Board. Source corpora and unpublished submissions are not distributed here.</footer>
</main></body></html>"""


def bundle(folder, existing_dates, latest_bytes):
    folder = Path(folder)
    corpus, _ = validate_public(folder)
    export = newsroom.load(folder / "public-export.json")
    for name in ("public.html", "public-draft.json", "public-review.json", "corpus.json"):
        if digest(folder / name) != export["sha256"][name]:
            raise ValueError(f"Public export changed: {name}; review and prepare again")
    day = export["date"]
    if day != corpus["date"]:
        raise ValueError("Public export date differs from the reviewed issue")
    dates = sorted(set([*existing_dates, day]))
    archive = archive_html(dates).encode()
    html = (folder / "public.html").read_bytes()
    if day != dates[-1] and latest_bytes is None:
        raise ValueError("Latest issue content is required when publishing an older edition")
    return {
        ".nojekyll": b"", "index.html": archive,
        f"issues/{day}/index.html": html,
        "latest/index.html": html if day == dates[-1] else latest_bytes,
    }


def require_immutable(existing, proposed):
    if existing is not None and existing != proposed:
        raise ValueError("Published dated issues are immutable; a correction needs separate authorisation")


def verify_bytes(expected, observed):
    if observed != expected:
        raise ValueError("The live page does not yet match the published HTML")


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self):
        token = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com", "--user", LOGIN],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if not token:
            raise GitHubError("No stored personal GitHub credential")
        self.env = {**os.environ, "GH_TOKEN": token, "GH_PROMPT_DISABLED": "1"}

    def request(self, method, path, payload=None):
        command = ["gh", "api", "--hostname", "github.com", "--method", method,
                   f"repos/{REPO}/{path}"]
        if payload is not None:
            command += ["--input", "-"]
        result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                                text=True, capture_output=True, env=self.env)
        if result.returncode:
            raise GitHubError(result.stderr.strip())
        return json.loads(result.stdout) if result.stdout.strip() else None


def read_blob(api, sha):
    blob = api.request("GET", f"git/blobs/{sha}")
    if blob["encoding"] != "base64":
        raise ValueError("Unsupported GitHub blob encoding")
    return base64.b64decode(blob["content"])


def repository_snapshot(api):
    ref = api.request("GET", f"git/ref/heads/{BRANCH}")
    head = ref["object"]["sha"]
    commit = api.request("GET", f"git/commits/{head}")
    tree = api.request("GET", f"git/trees/{commit['tree']['sha']}?recursive=1")
    if tree.get("truncated"):
        raise ValueError("Repository tree was truncated; refusing an incomplete archive update")
    blobs = {}
    for entry in tree["tree"]:
        if entry["type"] == "blob":
            if entry["mode"] != "100644":
                raise ValueError("Publication repository contains an unsupported executable or symlink")
            blobs[entry["path"]] = entry["sha"]
    return head, commit["tree"]["sha"], blobs


def push(folder, api):
    folder = Path(folder)
    # No workspace path or wildcard is ever passed to an upload command.
    head, tree_sha, blobs = repository_snapshot(api)
    existing_dates = []
    for path in blobs:
        match = re.fullmatch(r"issues/(\d{4}-\d{2}-\d{2})/index\.html", path)
        if match:
            existing_dates.append(match[1])
    latest_path = f"issues/{max(existing_dates)}/index.html" if existing_dates else None
    latest = read_blob(api, blobs[latest_path]) if latest_path else None
    files = bundle(folder, existing_dates, latest)
    day = newsroom.load(folder / "public-export.json")["date"]
    dated_path = f"issues/{day}/index.html"
    existing = read_blob(api, blobs[dated_path]) if dated_path in blobs else None
    require_immutable(existing, files[dated_path])
    changed = []
    for path, content in files.items():
        sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if blobs.get(path) != sha:
            changed.append({"path": path, "mode": "100644", "type": "blob", "content": content.decode()})
    if changed:
        updated_tree = api.request("POST", "git/trees", {"base_tree": tree_sha, "tree": changed})
        updated_commit = api.request("POST", "git/commits", {
            "message": f"Publish The Board Chronicle for {day}",
            "tree": updated_tree["sha"], "parents": [head],
        })
        # Non-forced update fails rather than overwriting a concurrent publisher.
        api.request("PATCH", f"git/refs/heads/{BRANCH}", {"sha": updated_commit["sha"], "force": False})
        head = updated_commit["sha"]
    newsroom.write_json(folder / "publication-pending.json", {
        "repository": REPO, "commit": head, "date": day,
        "files": {path: hashlib.sha256(content).hexdigest() for path, content in files.items()},
    })
    return files, head


def verify_live_files(files, head, attempts=12):
    for attempt in range(attempts):
        try:
            for path, expected in files.items():
                if path == ".nojekyll":
                    continue
                url = BASE_URL + path
                result = subprocess.run(
                    ["curl", "--silent", "--show-error", "--fail", "--max-time", "30",
                     "--location", "--proto", "=https", "--proto-redir", "=https",
                     "--header", "Cache-Control: no-cache", f"{url}?build={head}"],
                    capture_output=True, check=True,
                )
                verify_bytes(expected, result.stdout)
            return
        except (subprocess.CalledProcessError, ValueError) as error:
            if attempt + 1 == attempts:
                raise RuntimeError(f"GitHub commit saved, but live publication remains unverified: {error}") from error
            print(f"Waiting for Pages deployment ({attempt + 1}/{attempts}): {error}", file=sys.stderr)
            time.sleep(20)

def verify_live(folder, files, head, attempts=12):
    folder = Path(folder)
    verify_live_files(files, head, attempts)
    newsroom.write_json(folder / "publication.json", {
        "repository": REPO, "commit": head,
        "url": BASE_URL + f"issues/{newsroom.load(folder / 'public-export.json')['date']}/",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "files": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()},
    })
    pending = folder / "publication-pending.json"
    if pending.exists():
        pending.unlink()


def published_status(day, api, attempts=12):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("Non-canonical issue date")
    head, _, blobs = repository_snapshot(api)
    dated_path = f"issues/{day}/index.html"
    if dated_path not in blobs:
        return {"date": day, "status": "not_published", "commit": head}
    paths = (dated_path, "index.html", "latest/index.html")
    if any(path not in blobs for path in paths):
        raise ValueError("Published issue has an incomplete archive or latest page")
    files = {path: read_blob(api, blobs[path]) for path in paths}
    verify_live_files(files, head, attempts)
    return {"date": day, "status": "verified", "commit": head,
            "url": BASE_URL + f"issues/{day}/"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish", "status"))
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "status":
            print(json.dumps(published_status(str(args.folder), GitHub()), indent=2))
        elif args.command == "prepare":
            print(prepare(args.folder))
        else:
            files, head = push(args.folder, GitHub())
            verify_live(args.folder, files, head)
            print(newsroom.load(args.folder / "publication.json")["url"])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"publication error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
