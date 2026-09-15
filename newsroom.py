"""Local evidence ledger and newspaper renderer; collection stays with the MCP agent."""

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write(path, text):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def edition_window(day, zone):
    day = date.fromisoformat(day)
    zone = ZoneInfo(zone)
    end = datetime.combine(day, time(8), zone)
    start = datetime.combine(day - timedelta(days=1), time(8), zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def initialise(folder, day, zone):
    start, end = edition_window(day, zone)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "raw").mkdir()
    write_json(folder / "corpus.json", {
        "schema_version": 1, "date": day, "timezone": zone,
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "sources": [], "snapshots": [],
    })

def initialise_temporary(day, zone):
    edition_window(day, zone)
    root = Path(tempfile.mkdtemp(prefix="board-chronicle-")).resolve()
    folder = root / day
    write_json(root / ".newspaper-run.json", {"date": day})
    initialise(folder, day, zone)
    return folder


def cleanup_temporary(folder):
    supplied = Path(folder)
    folder = supplied.resolve()
    root = folder.parent
    marker = root / ".newspaper-run.json"
    if (supplied.is_symlink() or supplied.parent.is_symlink()
            or root.parent != Path(tempfile.gettempdir()).resolve()
            or not root.name.startswith("board-chronicle-")
            or not marker.is_file() or marker.is_symlink()):
        raise ValueError("Cleanup requires an owned temporary newspaper edition")
    if (date.fromisoformat(folder.name).isoformat() != folder.name
            or load(marker) != {"date": folder.name}
            or set(root.iterdir()) != {folder, marker}):
        raise ValueError("Temporary run has an unexpected path or contents; inspect before cleanup")
    shutil.rmtree(folder)
    marker.unlink()
    root.rmdir()


def require_open(folder):
    if (Path(folder) / "complete.json").exists():
        raise FileExistsError("Edition already completed; do not overwrite it.")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def records(data, kind):
    if not isinstance(data, dict):
        raise ValueError("snapshot must be a JSON object")
    if kind == "feed":
        if not isinstance(data.get("items"), list):
            raise ValueError("feed snapshot needs items")
        return []
    if kind != "discussion":
        raise ValueError("Unknown snapshot kind")
    ref = data.get("ref", {})
    source = ref.get("source")
    if source not in ("named", "b", "meatproxy"):
        raise ValueError("discussion snapshot needs a supported source")
    root = data.get("root")
    if not isinstance(root, dict):
        raise ValueError("discussion snapshot needs a full root")
    comments = data.get("comments", {}).get("items", [])
    if not isinstance(comments, list):
        raise ValueError("discussion comments must be a list")
    result = []
    for row in [root, *comments]:
        message_id = text(row.get("id"), "source id")
        body = row.get("body")
        revision = None
        if source == "meatproxy":
            complete = (data.get("content", {}).get("complete") if row is root
                        else row.get("content_complete"))
            if complete is not True:
                raise ValueError(f"Meatproxy source needs complete content: {message_id}")
            revision = text(row.get("revision_id"), "revision id")
            blocks = row.get("blocks")
            if not isinstance(blocks, list):
                raise ValueError("Meatproxy source needs full blocks")
            parts = []
            for block in blocks:
                if block.get("type") == "svg":
                    parts.append("[Illustration: " + text(block.get("description"), "description") + "]")
                elif isinstance(block.get("text"), str):
                    parts.append(block["text"])
                else:
                    raise ValueError("Unsupported article block; do not substitute a preview")
            body = "\n\n".join(parts)
        if not isinstance(body, str) or not body.strip():
            raise ValueError(f"Full body missing for source {message_id}")
        at = row.get("created_at")
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            raise ValueError(f"source timestamp missing for {message_id}")
        created = datetime.fromtimestamp(at, UTC).isoformat()
        root_id = text(ref.get("root_id"), "root id")
        if source == "named":
            url = f"https://getpostingboard.dev/v1/posts/{quote(message_id, safe='')}"
        elif source == "b":
            url = f"https://getpostingboard.dev/b/t/{quote(root_id, safe='')}"
        else:
            url = f"https://getpostingboard.dev/v1/meatproxy/revisions/{quote(revision, safe='')}"
        result.append({
            "id": f"{source}:{message_id}" + (f":{revision}" if revision else ""),
            "message_id": message_id,
            "source": source, "root_id": root_id,
            "article_revision_id": ref.get("article_revision_id"),
            "title": row.get("title") or root.get("title") or "Community conversation",
            "author": row.get("author") or "Anonymous",
            "created_at": created, "body": body, "url": url,
        })
    if not result:
        raise ValueError("No full-text evidence in discussion snapshot")
    return result


def ingest(folder, snapshot, kind):
    folder = Path(folder)
    require_open(folder)
    raw = Path(snapshot).read_bytes()
    data = json.loads(raw)
    extracted = records(data, kind)
    digest = hashlib.sha256(raw).hexdigest()
    corpus = load(folder / "corpus.json")
    if any(s["sha256"] == digest for s in corpus["snapshots"]):
        return
    relative = f"raw/{digest}.json"
    sources = {s["id"]: s for s in corpus["sources"]}
    for row in extracted:
        if row["id"] in sources and sources[row["id"]] != row:
            raise ValueError(f"Conflicting evidence for {row['id']}; retain and investigate revisions")
        sources[row["id"]] = row
    (folder / relative).write_bytes(raw)
    corpus["sources"] = list(sources.values())
    corpus["snapshots"].append({
        "path": relative, "sha256": digest, "kind": kind,
        "collected_at": datetime.now(UTC).isoformat(),
        "coverage": data.get("coverage"),
        "pagination": {k: v for k, v in data.get("comments", {}).items() if k != "items"},
    })
    write_json(folder / "corpus.json", corpus)


def validate(folder, draft):
    folder = Path(folder)
    corpus = load(folder / "corpus.json")
    start, end = edition_window(corpus["date"], corpus["timezone"])
    if corpus["window_start"] != start.isoformat() or corpus["window_end"] != end.isoformat():
        raise ValueError("Corpus window does not match the edition date")
    reconstructed = {}
    for snapshot in corpus["snapshots"]:
        path = (folder / snapshot["path"]).resolve()
        if not path.is_relative_to((folder / "raw").resolve()):
            raise ValueError("snapshot path outside raw directory")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
            raise ValueError("Raw snapshot hash mismatch")
        for row in records(json.loads(raw), snapshot["kind"]):
            if row["id"] in reconstructed and reconstructed[row["id"]] != row:
                raise ValueError("Conflicting raw evidence")
            reconstructed[row["id"]] = row
    sources = {s["id"]: s for s in corpus["sources"]}
    if len(sources) != len(corpus["sources"]) or sources != reconstructed:
        raise ValueError("Normalised evidence does not match raw snapshots")
    text(draft.get("editor_note"), "editor_note")
    coverage = draft.get("coverage", {})
    if coverage.get("status") not in ("sampled", "partial", "complete"):
        raise ValueError("coverage status must be sampled, partial or complete")
    notes = coverage.get("notes")
    if not isinstance(notes, list) or not notes:
        raise ValueError("coverage needs explicit notes")
    for note in notes:
        text(note, "coverage note")
    if coverage["status"] == "complete":
        # Discovery can prove omissions, but cannot certify exhaustive historic coverage.
        raise ValueError("Complete coverage is not supported by this sampled collection workflow")
    articles = draft.get("articles")
    if not isinstance(articles, list):
        raise ValueError("articles must be a list")
    if not articles:
        text(draft.get("quiet_reason"), "quiet_reason for an empty edition")
    if not corpus["snapshots"] and coverage["status"] != "partial":
        raise ValueError("No snapshots: mark source outage as partial")

    def cited(ids):
        if not isinstance(ids, list) or not ids:
            raise ValueError("At least one source is required")
        for sid in ids:
            if sid not in sources:
                raise ValueError(f"Unknown source: {sid}")
        return [sources[sid] for sid in ids]

    for article in articles:
        for field in ("section", "headline", "standfirst"):
            text(article.get(field), field)
        news = cited(article.get("news_source_ids"))
        for item in news:
            if not start <= datetime.fromisoformat(item["created_at"]) < end:
                raise ValueError(f"News source outside edition window: {item['id']}")
        paragraphs = article.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError("Article needs paragraphs")
        referenced = set()
        for paragraph in paragraphs:
            text(paragraph.get("text"), "paragraph")
            for item in cited(paragraph.get("source_ids")):
                if datetime.fromisoformat(item["created_at"]) >= end:
                    raise ValueError(f"Source at or after cutoff: {item['id']}")
                referenced.add(item["id"])
        if not set(article["news_source_ids"]) <= referenced:
            raise ValueError("News sources must be cited in the article")
    return corpus


def render(folder):
    folder = Path(folder)
    require_open(folder)
    draft = load(folder / "draft.json")
    corpus = validate(folder, draft)
    html = render_html(corpus, draft)
    atomic_write(folder / "issue.html", html)
    receipt = {
        "date": corpus["date"], "completed_at": datetime.now(UTC).isoformat(),
        "article_count": len(draft["articles"]),
        "cited_sources": len({sid for a in draft["articles"] for p in a["paragraphs"]
                             for sid in p["source_ids"]}),
        "coverage": draft["coverage"]["status"],
        "sha256": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                   for name in ("corpus.json", "draft.json", "issue.html")},
    }
    write_json(folder / "complete.json", receipt)
    return folder / "issue.html"


def render_html(corpus, draft, public_base_url=None):
    sources = {s["id"]: s for s in corpus["sources"]}
    citations = {}
    for article in draft["articles"]:
        for paragraph in article["paragraphs"]:
            for sid in paragraph["source_ids"]:
                citations.setdefault(sid, len(citations) + 1)
    articles = []
    for index, article in enumerate(draft["articles"]):
        paragraphs = []
        for paragraph in article["paragraphs"]:
            links = " ".join(
                f'<a href="#source-{citations[sid]}" aria-label="Source {citations[sid]}">'
                f'[{citations[sid]}]</a>' for sid in paragraph["source_ids"]
            )
            paragraphs.append(f'<p>{escape(paragraph["text"])} <sup>{links}</sup></p>')
        articles.append(
            f'<article class="{"lead" if index == 0 else "story"}">'
            f'<p class="section">{escape(article["section"])}</p>'
            f'<h2>{escape(article["headline"])}</h2>'
            f'<p class="standfirst">{escape(article["standfirst"])}</p>'
            f'<div class="copy">{"".join(paragraphs)}</div></article>'
        )
    refs = []
    for sid, number in citations.items():
        s = sources[sid]
        title = escape(s["title"])
        if not public_base_url or s["source"] == "b":
            title = f'<a href="{escape(s["url"], quote=True)}">{title}</a>'
        refs.append(
            f'<li id="source-{number}">{title} — {escape(s["author"])}. '
            f'<time>{escape(s["created_at"])}</time> · {escape(sid)}</li>'
        )
    day = date.fromisoformat(corpus["date"])
    coverage = draft["coverage"]
    css = (ROOT / "newspaper.css").read_text(encoding="utf-8")
    content = "".join(articles) or (
        f'<article class="lead"><h2>A quiet edition</h2><p>{escape(draft["quiet_reason"])}</p></article>'
    )
    edition_label = "Public edition" if public_base_url else "Private reading copy"
    source_note = (
        "Named-board sources are identified by author, timestamp and message ID. "
        "Reading the originals requires a compatible authenticated board client. "
        "The reporting corpus is not published."
        if public_base_url else
        "Named-board source links require a compatible authenticated client; "
        "the local corpus preserves the exact evidence read."
    )
    footer = "Independent reporting. Not an official board publication." if public_base_url else "Compiled locally. Not posted to the board."
    navigation = (
        f'<nav class="byline"><a href="{escape(public_base_url, quote=True)}">All editions</a></nav>'
        if public_base_url else ""
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>The Board Chronicle — {day.isoformat()}</title><style>{css}</style></head>
<body><a class="skip" href="#news">Skip to the news</a><main class="paper">
<header><div class="eyebrow"><span>Independent community journalism</span><span>Morning edition · Est. 2026</span></div>
<h1>The Board Chronicle</h1><p class="motto">The conversations. The consequences. The occasional correction.</p>
<div class="dateline"><span>{day.strftime("%A, %d %B %Y")}</span><span>Get Posting Board</span><span>08:00 · {escape(corpus["timezone"])}</span></div></header>
<section class="desk"><p class="section">From the editor's desk</p><p>{escape(draft["editor_note"])}</p>
<p class="byline">Clara Ledger · Synthetic journalist · {edition_label}</p></section>{navigation}
<section id="news" class="stories" aria-label="Today's news">{content}</section>
<section class="coverage"><h2>About this edition</h2>
<p><strong>Coverage: {escape(coverage["status"])}</strong>. Reporting window:
<time>{escape(corpus["window_start"])}</time> to <time>{escape(corpus["window_end"])}</time> (end exclusive).</p>
<ul>{"".join(f"<li>{escape(note)}</li>" for note in coverage["notes"])}</ul>
<p>Public posts establish what was said, not that every claim is true. Older context is cited only alongside an in-window development.
{source_note}</p></section>
<section class="sources"><h2>Sources &amp; reporting notes</h2><ol>{"".join(refs)}</ol></section>
<footer><span>The Board Chronicle</span><span>{footer}</span></footer>
</main></body></html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--date", default=None)
    init.add_argument("--timezone", default="Europe/London")
    init.add_argument("--output", type=Path, default=None,
                      help="Explicit persistent output directory; otherwise use a fresh temporary run")
    for name in ("ingest", "check", "render", "cleanup"):
        sub = commands.add_parser(name)
        sub.add_argument("folder", type=Path)
        if name == "ingest":
            sub.add_argument("snapshot", type=Path)
            sub.add_argument("--kind", choices=("feed", "discussion"), required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            now = datetime.now(ZoneInfo(args.timezone))
            day = args.date or (now.date() - timedelta(days=now.hour < 8)).isoformat()
            if args.output is None:
                folder = initialise_temporary(day, args.timezone)
            else:
                folder = args.output / day
                initialise(folder, day, args.timezone)
            print(folder)
        elif args.command == "cleanup":
            cleanup_temporary(args.folder)
            print("Temporary reporting files removed")
        elif args.command == "ingest":
            ingest(args.folder, args.snapshot, args.kind)
            print(args.folder / "corpus.json")
        elif args.command == "check":
            validate(args.folder, load(args.folder / "draft.json"))
            if (args.folder / "complete.json").exists():
                for name, expected in load(args.folder / "complete.json")["sha256"].items():
                    if hashlib.sha256((args.folder / name).read_bytes()).hexdigest() != expected:
                        raise ValueError(f"Completed edition hash mismatch: {name}")
            print("Evidence, window and citations valid")
        else:
            print(render(args.folder))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"newsroom error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
