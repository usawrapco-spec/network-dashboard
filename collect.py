#!/usr/bin/env python3
"""Collect engagement stats for the video news network and write data.json.

Reads /workspace/marketing/social/activity-log.csv, keeps only Map Desk
(Money, World, Places), Harbor News, and Quint Valley News posts that have a
public post URL, then fetches YouTube (yt-dlp), X (public FixTweet / syndication
APIs), and Facebook Reel view counts (yt-dlp). Facebook likes, comments, and
non-Reel posts are recorded as n/a when a public fetch cannot see them.

Usage:
    python3 collect.py
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

HERE = Path(__file__).resolve().parent
ACTIVITY_LOG = Path("/workspace/marketing/social/activity-log.csv")
DATA_PATH = HERE / "data.json"
STATS_DIR = HERE / "stats"

YT_DLP = "yt-dlp"

INCLUDE_BRANDS = (
    "map desk",
    "mapdesk",
    "harbor news",
    "harbornews",
    "quint valley",
    "quintvalley",
)
SKIP_BRANDS = (
    "usa wrap",
    "usawrap",
    "dekwave",
    "savajay",
)

POST_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|mobile\.)?(?:"
    r"youtube\.com/(?:shorts/|watch\?v=)[A-Za-z0-9_-]+"
    r"|youtu\.be/[A-Za-z0-9_-]+"
    r"|x\.com/[^/\s]+/status/\d+"
    r"|twitter\.com/[^/\s]+/status/\d+"
    r"|facebook\.com/reel/\d+"
    r"|facebook\.com/watch/?\?v=\d+"
    r"|facebook\.com/photo/?\?fbid=\d+"
    r"|facebook\.com/permalink\.php\?[^\s\"'<>]+"
    r"|facebook\.com/[^/\s]+/(?:posts|videos|photos)/[^\s\"'<>]+"
    r"|instagram\.com/(?:p|reel|reels)/[A-Za-z0-9_-]+"
    r")",
    re.I,
)

PHONE_RE = re.compile(
    r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]\d{4}"
)
# Never publish personal names on the public dashboard.
NAME_RE = re.compile(r"\b(Chance Wallace|Chance the Wrapper|Chance|Wallace)\b", re.I)

POSTED_HINTS = (
    "posted",
    "published",
    "first post",
    "confirmed published",
)
SKIP_ACTION_HINTS = (
    "file work",
    "nothing posted",
    "read-only",
    "blocked",
    "draft only",
    "delete posts",
    "not posted",
    "did not",
    "scheduled only",
    "content plan",
)

CHANNELS = [
    {
        "brand": "Map Desk",
        "platform": "YouTube",
        "handle": "@MapDeskNews",
        "url": "https://www.youtube.com/@MapDeskNews",
        "followers_label": "subscribers",
        "kind": "youtube",
    },
    {
        "brand": "Map Desk",
        "platform": "X",
        "handle": "@MapDeskNews",
        "url": "https://x.com/MapDeskNews",
        "followers_label": "followers",
        "kind": "x",
    },
    {
        "brand": "Map Desk",
        "platform": "Facebook",
        "handle": "Map Desk",
        "url": "https://www.facebook.com/profile.php?id=61596875401709",
        "followers_label": "followers",
        "kind": "facebook",
    },
    {
        "brand": "Harbor News",
        "platform": "Facebook",
        "handle": "Harbor News",
        "url": "https://www.facebook.com/profile.php?id=61596429704380",
        "followers_label": "followers",
        "kind": "facebook",
    },
    {
        "brand": "Quint Valley News",
        "platform": "Facebook",
        "handle": "Quint Valley News",
        "url": "https://www.facebook.com/profile.php?id=61596620650836",
        "followers_label": "followers",
        "kind": "facebook",
    },
]

PLATFORM_NOTES = {
    "YouTube": "Views and likes via yt-dlp. Comment counts are shown when YouTube exposes them; otherwise n/a.",
    "X": "Impressions, likes, reposts, and replies from the public FixTweet/syndication APIs (same public_metrics the X read tools return). Followers from the public profile payload.",
    "Facebook": "Reel view counts via yt-dlp on the public video page. Likes, comments, shares, photo posts, and permalink posts are n/a without a Facebook login.",
    "Instagram": "Not collected yet (no live Map Desk / Harbor News / Quint Valley News Instagram post links in the log).",
}


def now_pt() -> datetime:
    return datetime.now(PT)


def iso_pt(dt: datetime) -> str:
    return dt.astimezone(PT).isoformat(timespec="seconds")


def label_pt(dt: datetime) -> str:
    return dt.astimezone(PT).strftime("%a %b %-d, %Y, %-I:%M %p PT")


def sanitize(text: Optional[str]) -> str:
    if not text:
        return ""
    text = PHONE_RE.sub("", text)
    text = NAME_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -|,;:")


def clean_title(text: str, limit: int = 140) -> str:
    text = sanitize(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def lower(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def row_text(row: dict) -> str:
    return " ".join(
        [
            row.get("business_or_person") or "",
            row.get("account_used") or "",
            row.get("action") or "",
            row.get("post_link") or "",
            row.get("comment_text") or "",
        ]
    )


def is_video_network(text: str) -> bool:
    t = text.lower()
    if any(h in t for h in SKIP_BRANDS) and not any(h in t for h in INCLUDE_BRANDS):
        return False
    # Skip mixed USA Wrap + Harbor file-work rows unless the brand itself is a news brand.
    return any(h in t for h in INCLUDE_BRANDS)


def classify_brand(text: str) -> tuple[str, str]:
    t = text.lower()
    if "quint valley" in t or "quintvalley" in t:
        return "Quint Valley News", "Quint Valley News"
    if "harbor news" in t or "harbornews" in t:
        return "Harbor News", "Harbor News"
    if "map desk places" in t or "map desk: best places" in t or "mdp-" in t:
        return "Map Desk Places", "Map Desk"
    if "map desk world" in t or "map desk weekly" in t or "mdw-" in t or "md-20" in t:
        return "Map Desk World", "Map Desk"
    if "map desk money" in t or "mapdeskmoney" in t or "mdm-" in t:
        return "Map Desk Money", "Map Desk"
    if "map desk" in t or "mapdesk" in t:
        return "Map Desk", "Map Desk"
    return "Unknown", "Unknown"


def normalize_platform(raw: str, url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "x.com/" in u or "twitter.com/" in u:
        return "X"
    if "instagram.com/" in u:
        return "Instagram"
    if "facebook.com/" in u:
        return "Facebook"
    p = (raw or "").strip()
    if p in {"FB", "Facebook Group"}:
        return "Facebook"
    if p in {"X (file work)"}:
        return "X"
    return p or "Unknown"


def extract_urls(*parts: str) -> list[str]:
    found: list[str] = []
    for part in parts:
        if not part:
            continue
        for m in POST_URL_RE.finditer(part):
            url = m.group(0).rstrip(").,];'>\"")
            if url not in found:
                found.append(url)
    return found


def canonical_url(url: str) -> str:
    url = url.strip()
    m = re.search(r"(?:youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://youtube.com/shorts/{m.group(1)}"
    m = re.search(r"youtube\.com/watch\?v=([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    m = re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
    if m:
        return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    m = re.search(r"facebook\.com/reel/(\d+)", url)
    if m:
        return f"https://www.facebook.com/reel/{m.group(1)}"
    m = re.search(r"facebook\.com/watch/?\?v=(\d+)", url)
    if m:
        return f"https://www.facebook.com/reel/{m.group(1)}"
    m = re.search(r"facebook\.com/photo/\?fbid=(\d+)", url)
    if m:
        return f"https://www.facebook.com/photo/?fbid={m.group(1)}"
    m = re.search(r"instagram\.com/(?:reels|reel|p)/([A-Za-z0-9_-]+)", url)
    if m:
        kind = "reel" if "/reel" in url else "p"
        return f"https://www.instagram.com/{kind}/{m.group(1)}"
    return url


def is_usable_post_url(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if any(bad in u for bad in (
        "facebook.com/groups/",
        "facebook.com/login",
        "business.facebook.com",
        "instagram.com/accounts",
        "instagram.com/?",
    )):
        return False
    # Profile/home URLs with no post id
    if re.search(r"x\.com/[^/]+/?$", u) or re.search(r"instagram\.com/[^/]+/?$", u):
        return False
    return bool(POST_URL_RE.search(url)) or bool(re.search(
        r"facebook\.com/(?:reel|watch|photo|permalink\.php|.+/(?:posts|videos|photos)/)",
        u,
    ))


def looks_posted(action: str, link: str) -> bool:
    a = lower(action)
    if re.search(r"\bblocked\b", a):
        return False
    if re.search(r"nothing posted|not posted|did not post|draft only|delete posts", a):
        return False
    if link.startswith("(deleted)") or "deleted" in lower(link):
        return False
    if re.search(r"\b(posted|published|first post|confirmed published)\b", a):
        return True
    if is_usable_post_url(link) and re.search(r"\b(post|reel|short|upload)\b", a):
        return True
    return False


POST_ID_RE = re.compile(r"\b((?:QVN|MD[MWP]?|H|N)\d{2,4})\b", re.I)


def title_from_action(action: str, fallback: str = "") -> str:
    action = sanitize(action)
    if re.search(r"christmas for kids", action, re.I):
        return "Christmas for Kids"
    if re.search(r"map desk world", action, re.I):
        return "Map Desk World daily briefing"
    if re.search(r"where americans moved", action, re.I):
        return "Where Americans Moved in 2025"
    patterns = [
        r'Short\s+"([^"]+)"',
        r'Reel\s+"([^"]+)"',
        r"Through Time card:\s*([^;(]+)",
        r"first post:\s*(.+?)(?:\s*\(|;|$)",
        r"Posted\s+(?:[A-Z]{1,4}\d{2,4}\s+)?(.+?)(?:\s+\(|;|$)",
    ]
    for pat in patterns:
        m = re.search(pat, action, re.I)
        if m:
            return clean_title(m.group(1))
    text = re.sub(
        r"(QA PASS.*|dup_check.*|dup_gate.*|AI-voice.*|AI label.*|Made with AI.*|switched FB.*)",
        "",
        action,
        flags=re.I,
    )
    text = re.sub(r"^(Posted|first post:|Created .+ and posted)\s*", "", text, flags=re.I)
    return clean_title(text or fallback)


def parse_when(date_s: str, time_s: str) -> Optional[datetime]:
    date_s = (date_s or "").strip()
    time_s = (time_s or "").replace("PT", "").strip()
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %I:%M%p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_s} {time_s}", fmt).replace(tzinfo=PT)
        except ValueError:
            continue
    try:
        return datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=PT)
    except ValueError:
        return None


def load_previous() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def previous_post_map(prev: dict) -> dict[str, dict]:
    out = {}
    for p in prev.get("posts") or []:
        link = p.get("link")
        if link:
            out[canonical_url(link)] = p
    return out


def to_int(value: Any) -> Optional[int]:
    if value is None or value == "" or value in {"NA", "N/A", "na", "n/a", "None"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def http_json(url: str, timeout: int = 20) -> Optional[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        pass
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "-A",
                headers["User-Agent"],
                "-H",
                "Accept: application/json,text/plain,*/*",
                "--max-time",
                str(timeout),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if proc.returncode == 0 and proc.stdout.strip().startswith("{"):
            return json.loads(proc.stdout)
    except Exception:
        return None
    return None


def ytdlp_json(url: str, extra: Optional[list[str]] = None, timeout: int = 60) -> Optional[dict]:
    cmd = [
        YT_DLP,
        "--no-update",
        "--skip-download",
        "--socket-timeout",
        "20",
        "-j",
    ]
    if extra:
        cmd.extend(extra)
    cmd.append(url)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  yt-dlp failed for {url}: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-1] if err else f"exit {proc.returncode}"
        print(f"  yt-dlp miss for {url}: {tail}", file=sys.stderr)
        return None
    # Flat playlists may emit one JSON object per line
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        return None
    if len(lines) == 1:
        try:
            return json.loads(lines[0])
        except json.JSONDecodeError:
            return None
    items = []
    for ln in lines:
        try:
            items.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return {"_items": items}


def fetch_youtube(url: str) -> dict:
    data = ytdlp_json(url)
    if not data or data.get("_items"):
        return {"ok": False, "source": "yt-dlp"}
    return {
        "ok": True,
        "source": "yt-dlp",
        "title": clean_title((data.get("title") or "").replace(" #Shorts", "")),
        "views": to_int(data.get("view_count")),
        "likes": to_int(data.get("like_count")),
        "comments": to_int(data.get("comment_count")),
        "shares": None,
        "impressions": None,
        "channel_followers": to_int(data.get("channel_follower_count")),
        "channel_url": data.get("channel_url") or data.get("uploader_url"),
        "handle": data.get("uploader_id") or data.get("channel"),
    }


def fetch_youtube_channel_videos() -> list[dict]:
    urls = [
        "https://www.youtube.com/@MapDeskNews/shorts",
        "https://www.youtube.com/@MapDeskNews/videos",
    ]
    seen = set()
    found = []
    for url in urls:
        data = ytdlp_json(url, extra=["--flat-playlist"], timeout=90)
        items = []
        if not data:
            continue
        if data.get("_items"):
            items = data["_items"]
        elif data.get("entries"):
            items = data["entries"]
        else:
            items = [data]
        for item in items:
            vid = item.get("id") or ""
            if not vid or vid in seen:
                continue
            seen.add(vid)
            title = item.get("title") or item.get("url") or vid
            link = f"https://youtube.com/shorts/{vid}"
            found.append({"id": vid, "title": clean_title(title.replace(" #Shorts", "")), "link": link})
    return found


def fetch_x_post(url: str) -> dict:
    m = re.search(r"status/(\d+)", url)
    if not m:
        return {"ok": False, "source": "none"}
    post_id = m.group(1)
    user_m = re.search(r"x\.com/([^/]+)/status", url)
    handle = user_m.group(1) if user_m else "MapDeskNews"

    fx = http_json(f"https://api.fxtwitter.com/{handle}/status/{post_id}")
    if fx and (fx.get("tweet") or fx.get("code") == 200):
        tweet = fx.get("tweet") or {}
        author = tweet.get("author") or {}
        return {
            "ok": True,
            "source": "fxtwitter",
            "title": clean_title(tweet.get("text") or ""),
            "views": to_int(tweet.get("views")),
            "likes": to_int(tweet.get("likes")),
            "comments": to_int(tweet.get("replies")),
            "shares": to_int(tweet.get("retweets")),
            "impressions": to_int(tweet.get("views")),
            "quotes": to_int(tweet.get("quotes")),
            "channel_followers": to_int(author.get("followers")),
            "handle": author.get("screen_name") or handle,
        }

    synd = http_json(
        f"https://cdn.syndication.twimg.com/tweet-result?id={post_id}&lang=en&token=0"
    )
    if synd and synd.get("id_str") == post_id or (synd and synd.get("favorite_count") is not None):
        user = (synd or {}).get("user") or {}
        views = None
        view_info = synd.get("views") or synd.get("view_count")
        if isinstance(view_info, dict):
            views = to_int(view_info.get("count") or view_info.get("count_str"))
        else:
            views = to_int(view_info)
        return {
            "ok": True,
            "source": "syndication",
            "title": clean_title(synd.get("text") or ""),
            "views": views,
            "likes": to_int(synd.get("favorite_count") or synd.get("likes")),
            "comments": to_int(synd.get("conversation_count") or synd.get("reply_count")),
            "shares": to_int(synd.get("retweet_count")),
            "impressions": views,
            "channel_followers": to_int(user.get("followers_count") or user.get("followers")),
            "handle": user.get("screen_name") or handle,
        }

    return {"ok": False, "source": "x-public"}


def fetch_x_user(handle: str) -> dict:
    handle = handle.lstrip("@")
    data = http_json(f"https://api.fxtwitter.com/{handle}")
    if not data:
        return {"ok": False}
    user = data.get("user") or data.get("twitter") or {}
    if not user or not isinstance(user, dict):
        return {"ok": False}
    return {
        "ok": True,
        "followers": to_int(user.get("followers") or (user.get("public_metrics") or {}).get("followers_count")),
        "handle": user.get("screen_name") or handle,
        "name": user.get("name"),
    }


def fetch_facebook(url: str) -> dict:
    # Reels / watch pages sometimes expose view_count via yt-dlp without login.
    data = ytdlp_json(url)
    if data and not data.get("_items"):
        title = data.get("title") or data.get("description") or ""
        # Keep the first sentence, drop credit dump.
        title = title.split("Credits")[0].split("1.")[0]
        if "|" in title:
            title = title.split("|")[0]
        return {
            "ok": True,
            "source": "yt-dlp",
            "title": clean_title(title),
            "views": to_int(data.get("view_count")),
            "likes": to_int(data.get("like_count")),
            "comments": to_int(data.get("comment_count")),
            "shares": to_int(data.get("repost_count")),
            "impressions": None,
        }
    return {
        "ok": False,
        "source": "none",
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "impressions": None,
        "note": "n/a without a Facebook login",
    }


def collect_from_log() -> tuple[list[dict], set[str]]:
    if not ACTIVITY_LOG.exists():
        raise SystemExit(f"activity log not found: {ACTIVITY_LOG}")

    with ACTIVITY_LOG.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    deleted_urls: set[str] = set()
    posts_by_url: dict[str, dict] = {}
    id_to_urls: dict[str, set[str]] = {}

    for row in rows:
        text = row_text(row)
        action = row.get("action") or ""
        link_raw = (row.get("post_link") or "").strip()
        row_ids = {m.upper() for m in POST_ID_RE.findall(action + " " + link_raw)}

        # Record deletions, including rows that name a post id (H133, H153) without a URL.
        if "delete posts" in lower(action) or (
            re.search(r"\b(deleted|removed)\b", lower(action + " " + link_raw))
            and row_ids
        ):
            for u in extract_urls(link_raw, action, row.get("comment_text") or ""):
                deleted_urls.add(canonical_url(u))
            for pid in row_ids:
                deleted_urls.update(id_to_urls.get(pid, set()))

        if not is_video_network(text):
            continue
        # If a skip brand is the only brand and news is mentioned only as "skipped Harbor News"
        biz = lower(row.get("business_or_person"))
        if any(h in biz for h in SKIP_BRANDS) and not any(h in biz for h in INCLUDE_BRANDS):
            continue

        urls = [u for u in extract_urls(link_raw, action) if is_usable_post_url(u)]
        if not urls and looks_posted(action, link_raw) and is_usable_post_url(link_raw):
            urls = [link_raw]

        if not urls:
            continue
        if not looks_posted(action, link_raw) and "confirmed published" not in lower(action):
            # Skip inventory / hold / file-work rows that merely mention a live URL
            if not looks_posted(action, urls[0]):
                continue

        when = parse_when(row.get("date") or "", row.get("time_pt") or "")
        # Prefer a published timestamp mentioned in the action, e.g. "published 6:30 AM PT"
        pub = re.search(
            r"published\s+(\d{1,2}:\d{2}\s*[AP]M)\s*PT",
            action,
            re.I,
        )
        if pub and (row.get("date") or ""):
            alt = parse_when(row.get("date") or "", pub.group(1))
            if alt:
                when = alt

        brand, family = classify_brand(text + " " + action)
        # Don't classify a Harbor+QVN file-work row as a mixed post without a clear brand
        if "harbor news + quint" in lower(text) and "quint" not in lower(action):
            brand, family = "Harbor News", "Harbor News"

        for url in urls:
            url = canonical_url(url)
            if not is_usable_post_url(url):
                continue
            for pid in row_ids:
                id_to_urls.setdefault(pid, set()).add(url)
            platform = normalize_platform(row.get("platform") or "", url)
            title = title_from_action(action)
            posts_by_url[url] = {
                "date": (when.date().isoformat() if when else (row.get("date") or "")),
                "time_pt": (when.strftime("%-I:%M %p") if when else (row.get("time_pt") or "")),
                "datetime_pt": iso_pt(when) if when else None,
                "brand": brand,
                "family": family,
                "platform": platform,
                "title": title,
                "link": url,
                "log_action": sanitize(action)[:240],
            }

    return list(posts_by_url.values()), deleted_urls


def merge_youtube_discoveries(posts: list[dict]) -> list[dict]:
    existing = {canonical_url(p["link"]) for p in posts}
    try:
        found = fetch_youtube_channel_videos()
    except Exception as exc:
        print(f"  YouTube channel scan failed: {exc}", file=sys.stderr)
        return posts
    for item in found:
        link = canonical_url(item["link"])
        if link in existing:
            continue
        title = item["title"]
        brand, family = classify_brand("map desk " + title)
        posts.append(
            {
                "date": "",
                "time_pt": "",
                "datetime_pt": None,
                "brand": brand,
                "family": family,
                "platform": "YouTube",
                "title": title,
                "link": link,
                "log_action": "Discovered on the @MapDeskNews YouTube channel",
            }
        )
        existing.add(link)
        print(f"  discovered YouTube video not yet in the log: {link}")
    return posts


def apply_stats(post: dict, stats: dict, prev: Optional[dict], fetched_at: datetime) -> dict:
    prev = prev or {}

    def pick(key: str):
        if stats.get("ok") and key in stats and stats[key] is not None:
            return stats[key]
        # Do not regress a known number to n/a when a fetch fails
        if prev.get(key) is not None:
            return prev[key]
        return stats.get(key)

    title = post.get("title") or ""
    fetched_title = (stats.get("title") or "").strip()
    if fetched_title:
        boilerplate = (
            not title
            or len(title) < 16
            or title.lower().startswith("posted")
            or title.lower().startswith("first post")
            or title.lower().startswith("post ")
            or title.lower().endswith(" reel")
        )
        # Prefer a longer, more specific fetched title (e.g. the YouTube/Facebook headline)
        if boilerplate or len(fetched_title) > len(title) + 8:
            title = fetched_title

    note = None
    if not stats.get("ok"):
        if post["platform"] == "Facebook":
            note = "n/a without a Facebook login"
        elif prev:
            note = f"Using last known stats; live fetch via {stats.get('source') or 'source'} failed"
        else:
            note = "Stats unavailable on this pass"
    elif post["platform"] == "Facebook":
        missing = [k for k in ("views", "likes", "comments", "shares") if pick(k) is None]
        if missing == ["likes", "comments", "shares"] or set(missing) == {"likes", "comments", "shares"}:
            note = "Views from the public Reel page; likes, comments, and shares are n/a without a Facebook login"
        elif missing:
            note = "Facebook did not expose " + ", ".join(missing) + " on the public page"

    out = {
        "date": post.get("date") or prev.get("date") or "",
        "time_pt": post.get("time_pt") or prev.get("time_pt") or "",
        "datetime_pt": post.get("datetime_pt") or prev.get("datetime_pt"),
        "brand": post.get("brand") or prev.get("brand"),
        "family": post.get("family") or prev.get("family"),
        "platform": post["platform"],
        "title": title or prev.get("title") or "Untitled post",
        "link": post["link"],
        "views": pick("views"),
        "likes": pick("likes"),
        "comments": pick("comments"),
        "shares": pick("shares"),
        "impressions": pick("impressions"),
        "last_updated": iso_pt(fetched_at) if stats.get("ok") else (prev.get("last_updated") or iso_pt(fetched_at)),
        "stats_source": stats.get("source") if stats.get("ok") else (prev.get("stats_source") or stats.get("source") or "none"),
        "stats_note": note,
    }
    return out


def empty_bucket() -> dict:
    return {"posts": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0, "views_known": 0, "likes_known": 0}


def add_to(bucket: dict, post: dict) -> None:
    bucket["posts"] += 1
    for key in ("views", "likes", "comments", "shares"):
        val = post.get(key)
        if val is not None:
            bucket[key] += val
            if key in ("views", "likes"):
                bucket[f"{key}_known"] += 1


def build_history(current: dict) -> list[dict]:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    history = []
    for path in sorted(STATS_DIR.glob("*.json")):
        try:
            snap = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        history.append(
            {
                "at": snap.get("generated_at"),
                "label": snap.get("generated_at_pt"),
                "views": (snap.get("totals") or {}).get("views"),
                "likes": (snap.get("totals") or {}).get("likes"),
                "comments": (snap.get("totals") or {}).get("comments"),
                "shares": (snap.get("totals") or {}).get("shares"),
                "posts": (snap.get("totals") or {}).get("posts"),
                "by_brand": (snap.get("totals") or {}).get("by_brand"),
                "by_platform": (snap.get("totals") or {}).get("by_platform"),
                "youtube_subscribers": next(
                    (
                        c.get("followers")
                        for c in snap.get("channels") or []
                        if c.get("platform") == "YouTube"
                    ),
                    None,
                ),
                "x_followers": next(
                    (
                        c.get("followers")
                        for c in snap.get("channels") or []
                        if c.get("platform") == "X"
                    ),
                    None,
                ),
            }
        )
    # Current snapshot is written after this, so append it here too
    history.append(
        {
            "at": current["generated_at"],
            "label": current["generated_at_pt"],
            "views": current["totals"]["views"],
            "likes": current["totals"]["likes"],
            "comments": current["totals"]["comments"],
            "shares": current["totals"]["shares"],
            "posts": current["totals"]["posts"],
            "by_brand": current["totals"]["by_brand"],
            "by_platform": current["totals"]["by_platform"],
            "youtube_subscribers": next(
                (
                    c.get("followers")
                    for c in current.get("channels") or []
                    if c.get("platform") == "YouTube"
                ),
                None,
            ),
            "x_followers": next(
                (
                    c.get("followers")
                    for c in current.get("channels") or []
                    if c.get("platform") == "X"
                ),
                None,
            ),
        }
    )
    # Dedup identical timestamps
    dedup = []
    seen = set()
    for item in history:
        key = item.get("at")
        if key in seen:
            # replace previous with later write of same hour if needed
            dedup = [d for d in dedup if d.get("at") != key]
        seen.add(key)
        dedup.append(item)
    return dedup


def main() -> int:
    fetched_at = now_pt()
    print(f"Collecting video news network stats at {label_pt(fetched_at)}")
    prev = load_previous()
    prev_map = previous_post_map(prev)

    posts, deleted = collect_from_log()
    if deleted:
        before = len(posts)
        posts = [p for p in posts if canonical_url(p["link"]) not in deleted]
        print(f"  excluded {before - len(posts)} deleted post(s)")

    posts = merge_youtube_discoveries(posts)
    posts.sort(key=lambda p: p.get("datetime_pt") or p.get("date") or "", reverse=True)

    print(f"  {len(posts)} live post(s) to score")

    x_followers = None
    yt_subscribers = None
    enriched = []
    for post in posts:
        url = post["link"]
        print(f"  {post['platform']:10} {url}")
        stats: dict
        if post["platform"] == "YouTube":
            stats = fetch_youtube(url)
            if stats.get("channel_followers") is not None:
                yt_subscribers = stats["channel_followers"]
        elif post["platform"] == "X":
            stats = fetch_x_post(url)
            if stats.get("channel_followers") is not None:
                x_followers = stats["channel_followers"]
        elif post["platform"] == "Facebook":
            stats = fetch_facebook(url)
        else:
            stats = {"ok": False, "source": "none", "note": "Platform not fetched yet"}
        enriched.append(apply_stats(post, stats, prev_map.get(canonical_url(url)), fetched_at))

    # Channel-level X followers if we didn't get them from a post
    if x_followers is None:
        user = fetch_x_user("MapDeskNews")
        if user.get("ok"):
            x_followers = user.get("followers")
    if x_followers is None:
        x_followers = next(
            (c.get("followers") for c in prev.get("channels") or [] if c.get("platform") == "X"),
            None,
        )
    if yt_subscribers is None:
        # Try a direct channel probe
        ch = ytdlp_json("https://www.youtube.com/@MapDeskNews", extra=["--playlist-items", "0"], timeout=45)
        if ch:
            yt_subscribers = to_int(ch.get("channel_follower_count") or ch.get("subscriber_count"))
        if yt_subscribers is None:
            yt_subscribers = next(
                (c.get("followers") for c in prev.get("channels") or [] if c.get("platform") == "YouTube"),
                None,
            )

    channels = []
    for ch in CHANNELS:
        entry = dict(ch)
        if ch["kind"] == "youtube":
            entry["followers"] = yt_subscribers
            entry["followers_available"] = yt_subscribers is not None
        elif ch["kind"] == "x":
            entry["followers"] = x_followers
            entry["followers_available"] = x_followers is not None
        else:
            entry["followers"] = None
            entry["followers_available"] = False
            entry["note"] = "n/a without a Facebook login"
        entry.pop("kind", None)
        channels.append(entry)

    totals = empty_bucket()
    by_brand: dict[str, dict] = {}
    by_platform: dict[str, dict] = {}
    by_family: dict[str, dict] = {}
    for post in enriched:
        add_to(totals, post)
        by_brand.setdefault(post["brand"], empty_bucket())
        add_to(by_brand[post["brand"]], post)
        by_platform.setdefault(post["platform"], empty_bucket())
        add_to(by_platform[post["platform"]], post)
        by_family.setdefault(post["family"], empty_bucket())
        add_to(by_family[post["family"]], post)

    payload = {
        "generated_at": iso_pt(fetched_at),
        "generated_at_pt": label_pt(fetched_at),
        "timezone": "America/Los_Angeles",
        "notes": PLATFORM_NOTES,
        "channels": channels,
        "totals": {
            **totals,
            "by_brand": by_brand,
            "by_platform": by_platform,
            "by_family": by_family,
        },
        "posts": enriched,
        "history": [],
    }
    payload["history"] = build_history(payload)

    STATS_DIR.mkdir(parents=True, exist_ok=True)
    snap_name = fetched_at.strftime("%Y-%m-%d-%H") + ".json"
    # Write a slightly slimmer snapshot (full payload, history omitted to avoid nesting)
    snapshot = dict(payload)
    snapshot.pop("history", None)
    (STATS_DIR / snap_name).write_text(json.dumps(snapshot, indent=2) + "\n")
    DATA_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {DATA_PATH}")
    print(f"Wrote {STATS_DIR / snap_name}")
    print(
        f"Totals: {totals['posts']} posts, {totals['views']} views, "
        f"{totals['likes']} likes, YT subs={yt_subscribers}, X followers={x_followers}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
