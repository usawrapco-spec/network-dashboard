#!/usr/bin/env python3
"""Build the scheduled-post queue.

Writes a gitignored full queue under dashboard/private/ (for the private
Grok Page mirror) and an allowlisted public payload plus stills and small
720p preview videos under dashboard/media/scheduled/ and scheduled.json
for the GitHub Pages dashboard. Read-only on Big Helper's content-plan.csv.
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
PUBLIC_MEDIA = HERE / "media" / "scheduled"
PUBLIC_JSON = HERE / "scheduled.json"
MAX_VIDEO_BYTES = 10 * 1024 * 1024

PHONE_RE = re.compile(
    r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]\d{4}"
)
NAME_RE = re.compile(r"\b(Chance Wallace|Chance the Wrapper|Chance|Wallace)\b", re.I)
WALLACE_RE = re.compile(r"\b(Chance Wallace|Wallace)\b", re.I)
CHANCE_NAME_RE = re.compile(r"\b(Chance the Wrapper|Chance)\b", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
STATUS_PUBLIC = {
    "Ready to post": "Ready",
    "Approved": "Approved",
    "Awaiting OK": "Awaiting OK",
    "Pending review": "Pending review",
    "Pending Google review": "Pending review",
}

NEWS_BRANDS = (
    "map desk", "mapdesk", "harbor news", "harbornews", "quint valley", "quintvalley",
)
BUSINESS_BRANDS = ("usa wrap", "usawrap", "dekwave", "savajay")
INCLUDE = NEWS_BRANDS + BUSINESS_BRANDS
PERSONAL_URL = re.compile(r"linkedin\.com/in", re.I)
CHANCE_CHANNEL = re.compile(r"linkedin \(chance\)|chance the wrapper", re.I)

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
    SOCIAL / "linkedin",
    SOCIAL / "linkedin-refresh" / "featured",
    Path("/workspace/marketing/linkedin/media"),
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
        if PERSONAL_URL.search(channel) or PERSONAL_URL.search(brand) or PERSONAL_URL.search(r.get("asset_link") or ""):
            continue
        is_chance = bool(CHANCE_CHANNEL.search(channel) or CHANCE_CHANNEL.search(brand))
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
            "brand": ("Chance the Wrapper" if is_chance else (re.sub(r"\s*\(@[^)]+\)\s*$", "", brand).strip() or brand)),
            "family": ("Chance the Wrapper" if is_chance else (re.sub(r"\s*\(@[^)]+\)\s*$", "", brand).strip() or brand)),
            "network": (
                "creator" if is_chance else
                ("news" if own_brand(brand) and any(h in brand.lower() for h in NEWS_BRANDS) else "business")
            ),
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



def sanitize_public(text: Optional[str], *, brand: str = "") -> str:
    if not text:
        return ""
    text = PHONE_RE.sub("", text)
    text = re.sub(r"(?m)^[ \t]*[·•][ \t]*", "", text)
    text = WALLACE_RE.sub("", text)
    if "chance the wrapper" not in (brand or "").lower():
        text = CHANCE_NAME_RE.sub("", text)
    text = EMAIL_RE.sub("", text)
    text = re.sub(r"/workspace\S+", "", text)
    text = re.sub(r"https?://(?:www\.)?linkedin\.com/in/\S+", "", text, flags=re.I)
    text = re.sub(r"(?im)^\s*(call|text|phone|tel)\.?\s*[:\-–]?\s*$", "", text)
    text = re.sub(r"(?i)\b(call|text)\s*\.?\s*(?=\n|#|$)", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" -|,;:.")


def normalize_channel(channel: str) -> str:
    c = (channel or "").strip()
    low = c.lower()
    if "youtube" in low:
        return "YouTube"
    if low == "x" or low.startswith("x ") or "twitter" in low:
        return "X"
    if "instagram" in low:
        return "Instagram"
    if "linkedin" in low:
        return "LinkedIn"
    if "facebook" in low:
        return "Facebook"
    return c or "Unknown"


def copy_public_still(src: Optional[str], dest_name: str) -> Optional[str]:
    if not src:
        return None
    path = Path(src)
    if not path.is_file() or not dest_name:
        return None
    PUBLIC_MEDIA.mkdir(parents=True, exist_ok=True)
    dest = PUBLIC_MEDIA / dest_name
    if path.resolve() != dest.resolve():
        try:
            shutil.copy2(path, dest)
        except Exception:
            still = make_still(path, dest)
            if not still:
                return None
    return f"media/scheduled/{dest_name}" if dest.is_file() else None


def copy_public_video(src: Optional[str], dest_name: str) -> Optional[str]:
    """Copy a preview MP4 if it is already <= 10 MB; otherwise try a compact 720p encode."""
    if not src:
        return None
    path = Path(src)
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".webm", ".mov"}:
        return None
    PUBLIC_MEDIA.mkdir(parents=True, exist_ok=True)
    dest = PUBLIC_MEDIA / dest_name
    size = path.stat().st_size
    try:
        if size <= MAX_VIDEO_BYTES:
            shutil.copy2(path, dest)
        else:
            cmd = [
                "ffmpeg", "-y", "-i", str(path),
                "-vf", "scale='min(720,iw)':-2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "28", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                str(dest),
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except Exception:
        if dest.exists():
            dest.unlink()
        return None
    if not dest.is_file() or dest.stat().st_size > MAX_VIDEO_BYTES:
        if dest.exists():
            dest.unlink()
        return None
    return f"media/scheduled/{dest_name}"


def public_item(item: dict[str, Any], *, allow_video: bool) -> Optional[dict[str, Any]]:
    status = STATUS_PUBLIC.get(item.get("status") or "")
    if not status:
        return None
    still_name = item.get("still_name") or (f"{slugify(item.get('id') or 'item')}.jpg")
    still = copy_public_still(item.get("still"), still_name)
    video = None
    if allow_video:
        video = copy_public_video(item.get("preview_video"), f"{slugify(item.get('id') or 'item')}-720.mp4")
    platforms = item.get("platforms") or ([item.get("platform")] if item.get("platform") else [])
    platforms = [normalize_channel(p) for p in platforms if p]
    # Deduplicate, keep order
    seen = set()
    plat = []
    for p in platforms:
        if p not in seen:
            seen.add(p)
            plat.append(p)
    brand = item.get("brand")
    pub = {
        "id": item.get("id"),
        "group_id": item.get("id"),
        "title": sanitize_public(re.sub(r"\s*\(same source as [^)]+\)\s*", "", item.get("title") or item.get("id") or "Scheduled post"), brand=brand or "")[:160],
        "caption": sanitize_public(item.get("caption") or "", brand=brand or ""),
        "brand": brand,
        "family": item.get("family") or brand,
        "network": item.get("network") or "news",
        "platforms": plat,
        "planned_date": item.get("planned_date") or "",
        "planned_time": item.get("planned_time") or "",
        "planned_tz": item.get("planned_tz") or "PT",
        "planned_at": item.get("planned_at"),
        "status": status,
        "kind": item.get("kind") or "plan",
        "still": still,
        "video": video,
    }
    if item.get("location"):
        pub["location"] = sanitize_public(item.get("location"), brand=brand or "")
    return pub


def merge_public_twins(items: list[dict[str, Any]], private_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse FB/IG/X twins of the same creative into one public item."""
    keys = {}
    for raw in private_rows:
        pid = raw.get("id")
        preview = (raw.get("preview_key") or "").strip().lower()
        title = (raw.get("title") or "").strip().lower()
        key = (
            raw.get("planned_date") or "",
            re.sub(r"\s+", " ", (raw.get("planned_time") or "").lower()),
            (raw.get("brand") or "").lower(),
            preview or title,
        )
        keys[pid] = key
    buckets: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for item in items:
        key = keys.get(item.get("id")) or (
            item.get("planned_date") or "",
            item.get("planned_time") or "",
            (item.get("brand") or "").lower(),
            (item.get("title") or "").lower(),
        )
        if key not in buckets:
            buckets[key] = dict(item)
            buckets[key]["platforms"] = list(item.get("platforms") or [])
            order.append(key)
        else:
            cur = buckets[key]
            for p in item.get("platforms") or []:
                if p not in cur["platforms"]:
                    cur["platforms"].append(p)
            if item.get("still") and not cur.get("still"):
                cur["still"] = item["still"]
            if item.get("video") and not cur.get("video"):
                cur["video"] = item["video"]
            if item.get("caption") and len(item["caption"]) > len(cur.get("caption") or ""):
                cur["caption"] = item["caption"]
            ids = [cur.get("id"), item.get("id")]
            cur["group_id"] = "-".join(sorted({i.lower() for i in ids if i}))
    return [buckets[k] for k in order]


FORBIDDEN_PUBLIC_KEYS = {
    "owner", "phone_flag", "notes", "source", "status_detail", "preview_key",
    "qa", "post_type", "preview_video", "still_name",
}


def audit_public_payload(payload: dict[str, Any]) -> None:
    blob = json.dumps(payload)
    if PHONE_RE.search(blob) or WALLACE_RE.search(blob) or "/workspace" in blob or "linkedin.com/in" in blob.lower():
        raise ValueError("public scheduled payload failed the privacy allowlist")
    for collection in (payload.get("items") or [], payload.get("group_shares") or []):
        for item in collection:
            bad = FORBIDDEN_PUBLIC_KEYS.intersection(item)
            if bad:
                raise ValueError(f"public scheduled item {item.get('id')} has forbidden keys: {bad}")


def write_public_queue(private_payload: dict[str, Any], fetched_at: datetime) -> dict[str, Any]:
    PUBLIC_MEDIA.mkdir(parents=True, exist_ok=True)
    # Clear previous public media, then rewrite from the current queue.
    for old in PUBLIC_MEDIA.glob("*"):
        if old.is_file():
            old.unlink()
    ready = []
    for item in private_payload.get("ready") or []:
        pub = public_item(item, allow_video=True)
        if pub:
            ready.append(pub)
    planned = []
    for item in private_payload.get("planned") or []:
        pub = public_item(item, allow_video=False)
        if pub:
            planned.append(pub)
    planned = merge_public_twins(planned, private_payload.get("planned") or [])
    for raw in private_payload.get("pending_gbp") or []:
        pub = {
            "id": raw.get("id") or raw.get("plan_id"),
            "group_id": raw.get("group_id") or raw.get("id"),
            "title": sanitize_public(raw.get("title") or "Google Business post", brand=raw.get("brand") or ""),
            "caption": sanitize_public(raw.get("caption") or "", brand=raw.get("brand") or ""),
            "brand": raw.get("brand"),
            "family": raw.get("family") or raw.get("brand"),
            "network": raw.get("network") or "business",
            "platforms": ["Google Business"],
            "location": raw.get("location"),
            "planned_date": raw.get("date") or raw.get("planned_date") or "",
            "planned_time": raw.get("time_pt") or raw.get("planned_time") or "",
            "planned_tz": "PT",
            "planned_at": raw.get("datetime_pt") or raw.get("planned_at"),
            "status": "Pending review",
            "kind": "gbp",
            "still": None,
            "video": None,
        }
        planned.append(pub)
    shares = []
    for item in private_payload.get("group_shares") or []:
        pub = public_item(item, allow_video=False)
        if pub:
            pub["caption"] = ""
            pub["still"] = None
            pub["video"] = None
            shares.append(pub)
    payload = {
        "generated_at": fetched_at.isoformat(),
        "generated_at_pt": fetched_at.strftime("%Y-%m-%d %-I:%M %p PT"),
        "timezone": "America/Los_Angeles",
        "items": ready + planned,
        "group_shares": shares,
        "counts": {
            "ready": len(ready),
            "planned": len(planned),
            "group_shares": len(shares),
            "videos": sum(1 for i in ready + planned if i.get("video")),
        },
    }
    audit_public_payload(payload)
    PUBLIC_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"Wrote public scheduled queue: {payload['counts']['ready']} ready, "
        f"{payload['counts']['planned']} planned, "
        f"{payload['counts']['group_shares']} group shares, "
        f"{payload['counts']['videos']} video(s) → {PUBLIC_JSON}"
    )
    return payload


def write_scheduled_queue(fetched_at: Optional[datetime] = None, extra_pending: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
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
    extras = list(extra_pending or [])
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
        "pending_gbp": extras,
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
    return write_public_queue(payload, fetched_at)


if __name__ == "__main__":
    write_scheduled_queue()
