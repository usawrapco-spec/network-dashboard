#!/usr/bin/env python3
"""Build a private scheduled-post queue for the Grok Page preview.

Writes gitignored files under dashboard/private/. Never called into data.json
or GitHub Pages. Read-only on Big Helper's content-plan.csv.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
MT = ZoneInfo("America/Denver")

HERE = Path(__file__).resolve().parent
SOCIAL = Path("/workspace/marketing/social")
CONTENT_PLAN = SOCIAL / "content-plan.csv"
ACTIVITY_LOG = SOCIAL / "activity-log.csv"
RENDERS = SOCIAL / "video-news-network" / "renders"
EPISODES = SOCIAL / "video-news-network" / "episodes"
PRIVATE = HERE / "private"
# Private Grok Page titled "Map Desk – Scheduled posts". Rewrite it after each collect.
CANVAS_ID = "03805b8e-a489-4caa-b205-b683ab224b22"
CANVAS_TITLE = "Map Desk – Scheduled posts"
STILLS = PRIVATE / "stills"

NEWS_BRANDS = (
    "map desk", "mapdesk", "harbor news", "harbornews", "quint valley", "quintvalley",
)
BUSINESS_BRANDS = ("usa wrap", "usawrap", "dekwave", "savajay")
INCLUDE = NEWS_BRANDS + BUSINESS_BRANDS
PERSONAL = re.compile(r"linkedin \(chance\)|chance the wrapper|linkedin\.com/in", re.I)

# Named network renders Chance should preview even when they are not on the plan yet.
NETWORK_READY = (
    {
        "id": "mdm-2026-11-24-prices",
        "brand": "Map Desk Money",
        "family": "Map Desk",
        "title": "Home-price change by state, year to 2026 Q3",
        "status": "Ready to post",
        "status_detail": "QA passed, dup gate cleared, not in the activity log yet",
        "platforms": ["YouTube", "Facebook", "X", "Instagram"],
        "planned_date": "2026-11-24",
        "planned_time": "Waiting to post",
        "planned_tz": "PT",
    },
    {
        "id": "mdm-2026-11-24-living",
        "brand": "Map Desk Money",
        "family": "Map Desk",
        "title": "Cost of living by state, 2024 price levels",
        "status": "Ready to post",
        "status_detail": "QA passed, dup gate cleared, not in the activity log yet",
        "platforms": ["YouTube", "Facebook", "X", "Instagram"],
        "planned_date": "2026-11-24",
        "planned_time": "Waiting to post",
        "planned_tz": "PT",
    },
    {
        "id": "mdm-2026-11-24-rent",
        "brand": "Map Desk Money",
        "family": "Map Desk",
        "title": "Where renters pay the most of their income, 2025",
        "status": "Ready to post",
        "status_detail": "QA passed, dup gate cleared, not in the activity log yet",
        "platforms": ["YouTube", "Facebook", "X", "Instagram"],
        "planned_date": "2026-11-24",
        "planned_time": "Waiting to post",
        "planned_tz": "PT",
    },
    {
        "id": "mdp-places-2026-11-24-sample",
        "brand": "Map Desk Places",
        "family": "Map Desk",
        "title": "Best places to dig for gems in America",
        "status": "Awaiting OK",
        "status_detail": "QA and dup passed; waiting for an OK before it posts",
        "platforms": ["YouTube", "Facebook", "X", "Instagram"],
        "planned_date": "2026-11-24",
        "planned_time": "After approval",
        "planned_tz": "PT",
    },
)

ASSET_DIRS = [
    SOCIAL / "brand" / "posts",
    SOCIAL / "x-usawrapco" / "posts",
    SOCIAL / "dekwave",
    SOCIAL / "savajay",
    SOCIAL / "harbor-news",
    SOCIAL / "quint-valley-news",
    SOCIAL / "assets",
    SOCIAL,
]


def now_pt() -> datetime:
    return datetime.now(PT)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "item")[:48]


def own_brand(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in INCLUDE)


def caption_from_render(captions: dict) -> str:
    fb = captions.get("facebook")
    if isinstance(fb, str) and fb.strip():
        return fb.strip()
    yt = captions.get("youtube_shorts")
    if isinstance(yt, dict):
        desc = (yt.get("description") or yt.get("title") or "").strip()
        if desc:
            return desc
    ig = captions.get("instagram")
    if isinstance(ig, str) and ig.strip():
        return ig.strip()
    return ""


def title_from_render(captions: dict, fallback: str) -> str:
    yt = captions.get("youtube_shorts")
    if isinstance(yt, dict) and yt.get("title"):
        return str(yt["title"]).strip()
    return fallback


def qa_pass(render_dir: Path) -> bool:
    report = render_dir / "qa-report.md"
    if report.exists() and re.search(r"9x16:\s*PASS", report.read_text(), re.I):
        return True
    qa = render_dir / "qa" / "9x16" / "qa.json"
    if qa.exists():
        try:
            data = json.loads(qa.read_text())
            if str(data.get("result") or data.get("status") or "").lower() == "pass":
                return True
            if data.get("pass") is True:
                return True
        except Exception:
            pass
    return False


def is_published_row(row: dict) -> bool:
    """Only treat an activity-log row as already posted, not a build or plan note."""
    action = (row.get("action") or "").strip()
    link = (row.get("post_link") or "").strip()
    a = action.lower()
    if a.startswith("blocked") or a.startswith("nothing posted"):
        return False
    if re.search(r"\b(deleted|delete post)\b", a):
        return True
    posted = bool(re.match(r"^(posted|published|first post|post\b|created\b)", a))
    public = link.startswith("http") or bool(re.search(r"https?://", action))
    return posted and public


def logged_ids_and_slugs() -> set[str]:
    found: set[str] = set()
    if not ACTIVITY_LOG.exists():
        return found
    with ACTIVITY_LOG.open() as f:
        for row in csv.DictReader(f):
            if not is_published_row(row):
                continue
            blob = " ".join(str(row.get(k) or "") for k in row)
            found.update(re.findall(r"\b(?:mdm|mdp|H|DW|SJ|MN|X|QVN|G)\d[\w-]*", blob, re.I))
            found.update(re.findall(r"mdm-2026-\d{2}-\d{2}-[a-z0-9-]+", blob, re.I))
            found.update(re.findall(r"mdp-[a-z0-9-]+", blob, re.I))
    return {x.lower() for x in found}


def parse_plan_dt(date_s: str, time_s: str) -> Optional[datetime]:
    date_s = (date_s or "").strip()
    raw = (time_s or "").strip()
    tz = PT
    if re.search(r"\bMT\b", raw, re.I):
        tz = MT
    t = re.sub(r"\s*(PT|MT|p\.m\.|a\.m\.)", lambda m: {"p.m.": "PM", "a.m.": "AM"}.get(m.group(1).lower(), ""), raw, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if not date_s:
        return None
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %I:%M%p", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(f"{date_s} {t}".strip() if " " in fmt else date_s, fmt if t and " " in fmt else "%Y-%m-%d")
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def resolve_asset(asset_link: str, preview: str) -> Optional[Path]:
    candidates: list[Path] = []
    for raw in (asset_link, preview):
        raw = (raw or "").strip()
        if not raw:
            continue
        p = Path(raw)
        if p.is_file():
            return p
        if not p.is_absolute():
            candidates.append(SOCIAL / raw)
            candidates.append(SOCIAL / "brand" / "posts" / Path(raw).name)
        else:
            candidates.append(p)
        stem = Path(raw).name
        for folder in ASSET_DIRS:
            candidates.append(folder / stem)
            if not Path(stem).suffix:
                for ext in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
                    candidates.append(folder / f"{stem}{ext}")
    seen = set()
    for c in candidates:
        try:
            c = c.resolve()
        except Exception:
            continue
        if c in seen:
            continue
        seen.add(c)
        if c.is_file():
            return c
    # glob last resort on preview stem
    stem = Path(preview or asset_link or "").stem
    if stem and len(stem) >= 3:
        for folder in ASSET_DIRS[:6]:
            if not folder.is_dir():
                continue
            hits = list(folder.glob(f"{stem}.*"))
            files = [h for h in hits if h.is_file() and h.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4"}]
            if files:
                return files[0]
    return None


def make_still(src: Optional[Path], dest: Path, mid_sec: Optional[float] = None) -> Optional[str]:
    if src is None or not src.is_file():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        # Re-encode to a compact jpeg so canvas uploads stay under 5 MB.
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", "scale='min(720,iw)':-2",
            "-q:v", "4",
            str(dest),
        ]
    elif suffix in {".mp4", ".webm", ".mov"}:
        ss = "3"
        if mid_sec is not None:
            ss = str(mid_sec)
        cmd = [
            "ffmpeg", "-y", "-ss", ss, "-i", str(src),
            "-frames:v", "1",
            "-vf", "scale='min(720,iw)':-2",
            "-q:v", "4",
            str(dest),
        ]
    else:
        return None
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=40)
    except Exception:
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            try:
                shutil.copy2(src, dest)
            except Exception:
                return None
        else:
            return None
    return str(dest) if dest.is_file() else None


def network_items(logged: set[str], fetched_at: datetime) -> list[dict[str, Any]]:
    items = []
    for spec in NETWORK_READY:
        slug = spec["id"]
        if slug.lower() in logged:
            continue
        render = RENDERS / slug
        if not render.is_dir():
            continue
        captions = {}
        cap_path = render / "captions.json"
        if cap_path.exists():
            try:
                captions = json.loads(cap_path.read_text())
            except Exception:
                captions = {}
        still_src = None
        stills_dir = render / "stills"
        if stills_dir.is_dir():
            typed = sorted(stills_dir.glob("v_0*.jpg"))
            if typed:
                still_src = typed[min(2, len(typed) - 1)]
        preview_mp4 = next(iter(sorted(render.glob("preview-*-9x16-720.mp4"))), None)
        if still_src is None:
            still_src = render / "dup-still-9x16.jpg"
            if not still_src.is_file():
                still_src = preview_mp4
        dest = STILLS / f"{slug}.jpg"
        mid = 18.0 if "places" in slug else None
        still_path = make_still(still_src if still_src and Path(still_src).exists() else preview_mp4, dest, mid_sec=mid)
        items.append({
            "id": slug,
            "source": "network-render",
            "brand": spec["brand"],
            "family": spec["family"],
            "network": "news",
            "title": title_from_render(captions, spec["title"]),
            "caption": caption_from_render(captions),
            "platforms": spec["platforms"],
            "platform": ", ".join(spec["platforms"]),
            "planned_date": spec["planned_date"],
            "planned_time": spec["planned_time"],
            "planned_tz": spec["planned_tz"],
            "planned_at": None,
            "status": spec["status"],
            "status_detail": spec["status_detail"],
            "qa": "PASS" if qa_pass(render) else "unknown",
            "still": still_path,
            "still_name": f"{slug}.jpg" if still_path else None,
            "preview_video": str(preview_mp4) if preview_mp4 else None,
            "kind": "short",
            "post_type": "Short",
        })
    return items


def plan_items(logged: set[str], fetched_at: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not CONTENT_PLAN.exists():
        return [], []
    today = fetched_at.date()
    page_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    with CONTENT_PLAN.open() as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        status = (r.get("status") or "").strip()
        if not status.startswith("Approved"):
            continue
        brand = (r.get("brand") or "").strip()
        channel = (r.get("channel") or "").strip()
        if not own_brand(brand):
            continue
        if PERSONAL.search(channel) or PERSONAL.search(brand):
            continue
        pid = (r.get("id") or "").strip()
        if pid.lower() in logged:
            continue
        when = parse_plan_dt(r.get("date") or "", r.get("time") or "")
        date_s = (r.get("date") or "").strip()
        if date_s and date_s < today.isoformat():
            continue
        is_group = channel.lower().startswith("facebook group")
        rec: dict[str, Any] = {
            "id": pid,
            "source": "content-plan",
            "brand": re.sub(r"\s*\(@[^)]+\)\s*$", "", brand).strip() or brand,
            "family": re.sub(r"\s*\(@[^)]+\)\s*$", "", brand).strip() or brand,
            "network": "news" if own_brand(brand) and any(h in brand.lower() for h in NEWS_BRANDS) else "business",
            "title": (r.get("asset") or r.get("type") or pid).strip(),
            "caption": "" if is_group else (r.get("caption") or "").strip(),
            "platforms": [channel],
            "platform": channel,
            "planned_date": date_s,
            "planned_time": (r.get("time") or "").strip(),
            "planned_tz": "MT" if re.search(r"\bMT\b", r.get("time") or "") else "PT",
            "planned_at": when.astimezone(PT).isoformat() if when else None,
            "status": "Approved",
            "status_detail": "Approved on the content plan, not yet in the activity log",
            "qa": "",
            "still": None,
            "still_name": None,
            "preview_video": None,
            "kind": "group-share" if is_group else "plan",
            "post_type": (r.get("type") or "").strip(),
            "preview_key": (r.get("preview") or "").strip(),
        }
        if is_group:
            rec["title"] = "Group share of that week's newest feed post"
            rec["caption"] = ""
            group_rows.append(rec)
            continue
        asset = resolve_asset(r.get("asset_link") or "", r.get("preview") or "")
        if asset:
            dest_name = f"{slugify(pid)}{'.jpg'}"
            mid = 4.0 if asset.suffix.lower() in {".mp4", ".webm", ".mov"} else None
            still_path = make_still(asset, STILLS / dest_name, mid_sec=mid)
            rec["still"] = still_path
            rec["still_name"] = dest_name if still_path else None
            if asset.suffix.lower() in {".mp4", ".webm", ".mov"}:
                rec["preview_video"] = str(asset)
        page_rows.append(rec)
    def sort_key(item: dict[str, Any]):
        return item.get("planned_at") or item.get("planned_date") or "9999"
    page_rows.sort(key=sort_key)
    group_rows.sort(key=sort_key)
    return page_rows, group_rows


def write_scheduled_queue(fetched_at: Optional[datetime] = None) -> Path:
    fetched_at = fetched_at or now_pt()
    PRIVATE.mkdir(parents=True, exist_ok=True)
    STILLS.mkdir(parents=True, exist_ok=True)
    # Clear stale stills so removed items don't linger
    for old in STILLS.glob("*"):
        if old.is_file():
            old.unlink()
    logged = logged_ids_and_slugs()
    ready = network_items(logged, fetched_at)
    planned, groups = plan_items(logged, fetched_at)
    payload = {
        "generated_at": fetched_at.isoformat(),
        "generated_at_pt": fetched_at.strftime("%Y-%m-%d %-I:%M %p PT"),
        "timezone": "America/Los_Angeles",
        "note": (
            "Private scheduled queue only. Do not copy this file, stills, or captions "
            "into the public GitHub Pages repo."
        ),
        "ready": ready,
        "planned": planned,
        "group_shares": groups,
        "counts": {
            "ready": len(ready),
            "planned": len(planned),
            "group_shares": len(groups),
        },
    }
    out = PRIVATE / "scheduled.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    (PRIVATE / "README.md").write_text(
        "# Private scheduled queue\n\n"
        "This folder is gitignored and must never be pushed to GitHub Pages.\n\n"
        "Refresh: `python3 collect.py` (also writes this file) or "
        "`python3 scheduled.py`.\n\n"
        "After a refresh, regenerate the private Grok Page titled "
        "**Map Desk – Scheduled posts** from `scheduled.json` and `stills/` "
        "so Chance sees new QA-passed or Approved items automatically.\n"
    )
    print(
        f"Wrote private scheduled queue: {payload['counts']['ready']} ready, "
        f"{payload['counts']['planned']} planned, "
        f"{payload['counts']['group_shares']} group shares → {out}"
    )
    return out


if __name__ == "__main__":
    write_scheduled_queue()
