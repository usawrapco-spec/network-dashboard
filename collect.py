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
from datetime import datetime, timedelta
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

NEWS_BRANDS = (
    "map desk",
    "mapdesk",
    "harbor news",
    "harbornews",
    "quint valley",
    "quintvalley",
)
BUSINESS_BRANDS = (
    "usa wrap",
    "usawrap",
    "dekwave",
    "savajay",
)
INCLUDE_BRANDS = NEWS_BRANDS + BUSINESS_BRANDS
# Kept so older call sites can still skip non-network chatter.
SKIP_BRANDS = ()

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
        "network": "news",
    },
    {
        "brand": "Map Desk",
        "platform": "X",
        "handle": "@MapDeskNews",
        "url": "https://x.com/MapDeskNews",
        "followers_label": "followers",
        "kind": "x",
        "network": "news",
    },
    {
        "brand": "Map Desk",
        "platform": "Facebook",
        "handle": "Map Desk",
        "url": "https://www.facebook.com/profile.php?id=61596875401709",
        "followers_label": "followers",
        "kind": "facebook",
        "network": "news",
    },
    {
        "brand": "Harbor News",
        "platform": "Facebook",
        "handle": "Harbor News",
        "url": "https://www.facebook.com/profile.php?id=61596429704380",
        "followers_label": "followers",
        "kind": "facebook",
        "network": "news",
    },
    {
        "brand": "Quint Valley News",
        "platform": "Facebook",
        "handle": "Quint Valley News",
        "url": "https://www.facebook.com/profile.php?id=61596620650836",
        "followers_label": "followers",
        "kind": "facebook",
        "network": "news",
    },
    {
        "brand": "USA Wrap Co",
        "platform": "Facebook",
        "handle": "USA WRAP Co.",
        "url": "https://www.facebook.com/USAWRAPCO",
        "followers_label": "followers",
        "kind": "facebook",
        "network": "business",
    },
    {
        "brand": "USA Wrap Co",
        "platform": "Instagram",
        "handle": "@usawrapco_upfitting",
        "url": "https://www.instagram.com/usawrapco_upfitting/",
        "followers_label": "followers",
        "kind": "instagram",
        "network": "business",
    },
    {
        "brand": "USA Wrap Co",
        "platform": "X",
        "handle": "@usawrapco",
        "url": "https://x.com/usawrapco",
        "followers_label": "followers",
        "kind": "x",
        "network": "business",
    },
    {
        "brand": "DekWave",
        "platform": "Facebook",
        "handle": "DekWave",
        "url": "https://www.facebook.com/profile.php?id=61590781825933",
        "followers_label": "followers",
        "kind": "facebook",
        "network": "business",
    },
    {
        "brand": "DekWave",
        "platform": "Instagram",
        "handle": "@dekwaveprotect",
        "url": "https://www.instagram.com/dekwaveprotect/",
        "followers_label": "followers",
        "kind": "instagram",
        "network": "business",
    },
    {
        "brand": "Savajay",
        "platform": "Facebook",
        "handle": "Savajay",
        "url": "https://www.facebook.com/profile.php?id=61553803499989",
        "followers_label": "followers",
        "kind": "facebook",
        "network": "business",
    },
]

PLATFORM_NOTES = {
    "YouTube": "Views and likes via yt-dlp. Comment counts are shown when YouTube exposes them; otherwise n/a.",
    "X": "Impressions, likes, reposts, and replies from the public FixTweet/syndication APIs (same public_metrics the X read tools return). Followers from the public profile payload.",
    "Facebook": "Reel view counts via yt-dlp on the public video page. Likes, comments, shares, photo/permalink posts, and Story views are n/a without a login or a recorded in-account check. Stories expire after 24 hours and usually have no public URL.",
    "Instagram": "Feed post views and likes via yt-dlp when the public page exposes them. Story views and replies are n/a unless an in-account check has recorded a real number; Stories expire after 24 hours and usually have no public URL.",
    "Google Business": "Google Business posts have no public URL. This list shows the date, location, caption, and status. Published posts appear here; posts still in Pending review are on the Scheduled tab. Skipped posts are not listed.",
}


def now_pt() -> datetime:
    return datetime.now(PT)


def iso_pt(dt: datetime) -> str:
    return dt.astimezone(PT).isoformat(timespec="seconds")


def label_pt(dt: datetime) -> str:
    return dt.astimezone(PT).strftime("%a %b %-d, %Y, %-I:%M %p PT")


def sanitize_caption(text: Optional[str]) -> str:
    """Strip phones, names, emails and paths, but keep paragraph breaks."""
    if not text:
        return ""
    text = PHONE_RE.sub("", text)
    text = NAME_RE.sub("", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "", text)
    text = re.sub(r"/workspace\S+", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" -|,;:")


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


def is_own_brand(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in INCLUDE_BRANDS)


def network_for(family: str) -> str:
    f = (family or "").lower()
    if f in {"usa wrap co", "dekwave", "savajay"} or "usa wrap" in f:
        return "business"
    return "news"


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
    if "dekwave" in t:
        return "DekWave", "DekWave"
    if "savajay" in t:
        return "Savajay", "Savajay"
    if "usa wrap" in t or "usawrap" in t:
        return "USA Wrap Co", "USA Wrap Co"
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
    if not url:
        return ""
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


def is_story(action: str, link: str = "") -> bool:
    blob = f"{action} {link}"
    if re.search(r"\b(story posted|posted via business suite.*story|story,? no url)\b", blob, re.I):
        return True
    if (link or "").lower().startswith("(story"):
        return True
    # Avoid "6 stories" in a briefing or an unposted "News desk Story" row.
    return bool(re.search(r"\bpost\s+[A-Z0-9]+:\s*Story posted\b", action, re.I))


def looks_posted(action: str, link: str) -> bool:
    a = lower(action)
    # Only treat the post itself as blocked, not a later "live check blocked" note on a successful Posted row.
    if re.search(r"\bblocked\b", a) and not re.match(r"^(posted|post |first post|created )", a):
        return False
    if re.search(
        r"nothing posted|not posted|did not post|draft only|delete posts|"
        r"file work|inventoried|refreshed social media calendar|password reset|"
        r"profile update|cover photo updated|\bskipped\b|^built |^qa ",
        a,
    ):
        return False
    if (link or "").startswith("(deleted)") or "deleted" in lower(link):
        return False
    if "linkedin.com/in/" in lower(link):
        return False
    if is_story(action, link) and re.search(r"\b(posted|published)\b", a):
        return True
    if re.search(r"\b(posted|published|first post|confirmed published)\b", a):
        return True
    if is_usable_post_url(link) and re.search(r"\b(post|reel|short|upload)\b", a):
        return True
    if re.match(r"^shout-out post\b", a) and is_usable_post_url(link):
        return True
    return False


POST_ID_RE = re.compile(r"\b((?:QVN|MD[MWP]?|DW|SJ|SO|MN|L|G|X|U|H|N)\d{2,4})\b", re.I)


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



_PLAN_CACHE = None

def load_content_plan() -> list[dict]:
    global _PLAN_CACHE
    if _PLAN_CACHE is not None:
        return _PLAN_CACHE
    path = Path("/workspace/marketing/social/content-plan.csv")
    if not path.exists():
        _PLAN_CACHE = []
        return _PLAN_CACHE
    with path.open() as f:
        _PLAN_CACHE = list(csv.DictReader(f))
    return _PLAN_CACHE


def shoutout_plan_match(date_s: str, platform: str, url: str = "") -> tuple[str, str]:
    """Return (title, plan_id) for a shout-out we posted from our Page."""
    plat = (platform or "").lower()
    for row in load_content_plan():
        typ = (row.get("type") or "").lower()
        if "shout-out" not in typ and not (row.get("id") or "").upper().startswith("SO"):
            continue
        if (row.get("date") or "").strip() != (date_s or "").strip():
            continue
        ch = (row.get("channel") or "").lower()
        if plat and plat not in ch and ch not in plat:
            # Facebook vs FB
            if not (("facebook" in plat or plat == "fb") and "facebook" in ch):
                continue
        title = sanitize(row.get("asset") or row.get("caption") or "")
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title[:120], (row.get("id") or "").strip().upper()
    return "", ""



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
        if p.get("id"):
            out[p["id"]] = p
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

        # Record deletions, including rows that name a post id without a URL.
        if "delete posts" in lower(action) or re.search(
            r"\b(deleted|removed)\b", lower(action + " " + link_raw)
        ):
            for u in extract_urls(link_raw, action, row.get("comment_text") or ""):
                deleted_urls.add(canonical_url(u))
            for pid in row_ids:
                deleted_urls.update(id_to_urls.get(pid, set()))

        if not is_own_brand(text):
            continue
        # The business_or_person field must be one of our brands, not a customer
        # or group we only liked/commented on. A shout-out we posted from our
        # Page may name the other page in that field and still be our post.
        biz = lower(row.get("business_or_person"))
        account = row.get("account_used") or ""
        our_shoutout = bool(re.search(r"^shout-out post\b", action, re.I)) and is_own_brand(account)
        if not is_own_brand(biz) and not our_shoutout:
            continue
        # Never import personal LinkedIn.
        if "linkedin.com/in/" in lower(link_raw) or "linkedin.com/in/" in lower(action):
            continue

        urls = [u for u in extract_urls(link_raw, action) if is_usable_post_url(u)]
        if not urls and looks_posted(action, link_raw) and is_usable_post_url(link_raw):
            urls = [link_raw]

        story = is_story(action, link_raw) and looks_posted(action, link_raw)
        if not urls and not story:
            continue
        if urls and not looks_posted(action, link_raw) and "confirmed published" not in lower(action):
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
        if our_shoutout and not is_own_brand(brand):
            brand, family = classify_brand(account)
        # Don't classify a Harbor+QVN file-work row as a mixed post without a clear brand
        if "harbor news + quint" in lower(text) and "quint" not in lower(action):
            brand, family = "Harbor News", "Harbor News"

        entries = list(urls)
        if story and not entries:
            entries = [None]

        for url in entries:
            if url:
                url = canonical_url(url)
                if not is_usable_post_url(url):
                    continue
                for pid in row_ids:
                    id_to_urls.setdefault(pid, set()).add(url)
                platform = normalize_platform(row.get("platform") or "", url)
                key = url
            else:
                raw_plat = (row.get("platform") or "").split("+")[0].split("/")[0].strip()
                platform = {"FB": "Facebook", "Instagram": "Instagram", "Facebook": "Facebook"}.get(raw_plat, raw_plat or "Unknown")
                key = "story:" + (next(iter(sorted(row_ids)), "") or f"{brand}:{platform}:{row.get('date')}")
            title = "Story" if story else title_from_action(action)
            shout_pid = ""
            if our_shoutout:
                shout_title, shout_pid = shoutout_plan_match(row.get("date") or "", platform, url or "")
                if shout_title:
                    title = shout_title
            expires_at = None
            story_live = None
            if story and when:
                exp = when.astimezone(PT)
                exp = exp + timedelta(hours=24)
                expires_at = iso_pt(exp)
                story_live = now_pt() < exp
            action_blob = action + " " + (link_raw or "")
            episode_m = re.search(r"\b((?:mdm|mdw|mdp)[-a-z0-9]+)\b", action_blob, re.I)
            plan_ids = [x.upper() for x in re.findall(r"\b((?:H|DW|SJ|MN|SO|L|X|QVN|G)\d{3,})\b", action_blob, re.I)]
            if shout_pid and shout_pid not in plan_ids:
                plan_ids = [shout_pid] + plan_ids
            posts_by_url[key] = {
                "id": key if str(key).startswith("story:") else None,
                "episode": (episode_m.group(1).lower() if episode_m else None),
                "plan_id": plan_ids[0] if plan_ids else None,
                "plan_ids": plan_ids,
                "date": (when.date().isoformat() if when else (row.get("date") or "")),
                "time_pt": (when.strftime("%-I:%M %p") if when else (row.get("time_pt") or "")),
                "datetime_pt": iso_pt(when) if when else None,
                "brand": brand,
                "family": family,
                "network": network_for(family),
                "platform": platform,
                "title": title,
                "link": url,
                "kind": "story" if story else "post",
                "expires_at": expires_at,
                "story_live": story_live,
            }

    return list(posts_by_url.values()), deleted_urls




ROUTINE_CAPTION_RE = re.compile(
    r"written by the tuesday routine|no phone, email, url or handle",
    re.I,
)


def gbp_location(brand: str, account: str = "") -> str:
    text = f"{brand} {account}"
    low = text.lower()
    if "olympia" in low:
        return "USA Wrap Co · Olympia"
    if "dekwave" in low:
        return "DekWave"
    if "gig harbor" in low or "usa wrap" in low or "usawrap" in low:
        return "USA Wrap Co · Gig Harbor"
    return sanitize(brand) or "Google Business"


def gbp_brand(location: str) -> str:
    return "DekWave" if "dekwave" in (location or "").lower() else "USA Wrap Co"


def is_internal_gbp_caption(text: str) -> bool:
    return bool(ROUTINE_CAPTION_RE.search(text or ""))


def gbp_title(asset: str) -> str:
    title = sanitize(asset or "")
    title = re.sub(r"\((?:IMG|DSC|DSCN)[_-]?\d+[^)]*\)", "", title, flags=re.I)
    title = re.sub(r"\s{2,}", " ", title).strip(" -|,")
    return title or "Google Business post"


def collect_google_business() -> tuple[list[dict], list[dict]]:
    """Return (published, pending_review) Google Business items. No public URLs, no phones."""
    plan_rows: list[dict] = []
    plan_path = Path("/workspace/marketing/social/content-plan.csv")
    if plan_path.exists():
        with plan_path.open() as f:
            for row in csv.DictReader(f):
                if (row.get("channel") or "").strip() != "Google Business":
                    continue
                plan_rows.append(row)

    activity: list[dict] = []
    if ACTIVITY_LOG.exists():
        with ACTIVITY_LOG.open() as f:
            for row in csv.DictReader(f):
                if (row.get("platform") or "").strip().lower() != "google business":
                    continue
                action = row.get("action") or ""
                if re.search(r"\b(inventoried|history pass|skipped)\b", action, re.I):
                    continue
                activity.append(row)

    def caption_from_activity(date_s: str, location: str) -> str:
        for row in activity:
            loc = gbp_location(row.get("business_or_person") or "", row.get("account_used") or "")
            if (row.get("date") or "") != date_s:
                continue
            if loc != location:
                continue
            cap = sanitize_caption((row.get("comment_text") or "").strip())
            if cap and not is_internal_gbp_caption(cap):
                return cap
        return ""

    def activity_pending(date_s: str, location: str) -> bool:
        for row in activity:
            loc = gbp_location(row.get("business_or_person") or "", row.get("account_used") or "")
            if (row.get("date") or "") == date_s and loc == location and re.search(r"pending review", row.get("action") or "", re.I):
                return True
        return False

    published: list[dict] = []
    pending: list[dict] = []
    for row in plan_rows:
        status_raw = (row.get("status") or "").strip()
        low = status_raw.lower()
        if low.startswith("skip") or low.startswith("hold") or low.startswith("needs") or low.startswith("deleted"):
            continue
        location = gbp_location(row.get("brand") or "")
        brand = gbp_brand(location)
        date_s = (row.get("date") or "").strip()
        when = parse_when(row.get("date") or "", re.sub(r"~|\(Tue routine\)", "", row.get("time") or ""))
        cap = caption_from_activity(date_s, location)
        if not cap:
            cap = sanitize_caption((row.get("caption") or "").strip())
        if is_internal_gbp_caption(cap):
            cap = ""
        title = gbp_title(row.get("asset") or "")
        pending_flag = "pending" in low or activity_pending(date_s, location)
        published_flag = low.startswith("posted") or low.startswith("published")
        if not pending_flag and not published_flag:
            # Weekly placeholder slots with no real caption stay off the public site.
            continue
        item = {
            "id": f"gbp:{row.get('id')}",
            "plan_id": (row.get("id") or "").strip(),
            "group_id": f"gbp:{(row.get('id') or '').strip().lower()}",
            "date": date_s,
            "time_pt": when.strftime("%-I:%M %p") if when else "",
            "datetime_pt": iso_pt(when) if when else None,
            "brand": brand,
            "family": brand,
            "network": network_for(brand),
            "platform": "Google Business",
            "location": location,
            "title": title,
            "caption": cap,
            "link": None,
            "kind": "gbp",
            "status": "Pending review" if pending_flag else "Published",
            "views": None,
            "likes": None,
            "comments": None,
            "shares": None,
            "impressions": None,
            "last_updated": iso_pt(when) if when else None,
            "stats_source": "none",
            "stats_note": "Google Business posts have no public URL. Stats are n/a.",
        }
        if pending_flag:
            pending.append(item)
        else:
            published.append(item)
    return published, pending


def is_group_share_url(url: str) -> bool:
    return bool(re.search(r"facebook\.com/groups/\d+", url or "", re.I))


def is_posted_group_share(action: str, link: str) -> bool:
    a = lower(action)
    if re.search(r"\bblocked\b|nothing posted|did not post|not posted", a):
        return False
    if not (is_group_share_url(link) or "facebook.com/groups/" in lower(link or "")):
        # Allow an explicit share action even if the URL is only in the action text.
        if not re.search(r"facebook\.com/groups/\d+", action or "", re.I):
            return False
    return bool(re.search(
        r"(^group share\b|^shared page post to group\b|^shared .+ to group\b|\bgroup share\b.*\b(posted|published|shared)\b)",
        a,
    )) or bool(re.match(r"^(group share|shared page post)", a))


def group_name_from_row(row: dict) -> str:
    """Use a group name only when it already appears on the activity-log row, not from plan notes."""
    biz = (row.get("business_or_person") or "").strip()
    if not biz:
        return ""
    if is_own_brand(biz):
        return ""
    if NAME_RE.search(biz):
        return ""
    return sanitize(biz)


def collect_posted_group_shares() -> list[dict]:
    """Posted Facebook group shares, with public links, for the Facebook tab."""
    plan_meta: dict[str, dict] = {}
    plan_by_date_link: list[dict] = []
    if Path("/workspace/marketing/social/content-plan.csv").exists():
        with Path("/workspace/marketing/social/content-plan.csv").open() as f:
            for row in csv.DictReader(f):
                if (row.get("channel") or "").strip().lower() != "facebook group":
                    continue
                if not (row.get("status") or "").startswith("Posted"):
                    continue
                pid = (row.get("id") or "").strip()
                rec = {
                    "id": pid,
                    "brand": re.sub(r"\s*\(@[^)]+\)\s*$", "", row.get("brand") or "").strip() or "USA Wrap Co",
                    "title": sanitize((row.get("asset") or "").strip()) or "Group share",
                    "date": (row.get("date") or "").strip(),
                }
                if pid:
                    plan_meta[pid.upper()] = rec
                plan_by_date_link.append(rec)

    posts: list[dict] = []
    seen_links: set[str] = set()
    if not ACTIVITY_LOG.exists():
        return posts
    with ACTIVITY_LOG.open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        action = row.get("action") or ""
        link_raw = (row.get("post_link") or "").strip()
        urls = [u for u in extract_urls(link_raw, action) if is_group_share_url(u)]
        if not urls and is_group_share_url(link_raw):
            urls = [link_raw]
        if not urls:
            continue
        if not is_posted_group_share(action, link_raw or (urls[0] if urls else "")):
            continue
        # Brand comes from the account or a matching content-plan row, not from the group name.
        account = (row.get("account_used") or "") + " " + (row.get("business_or_person") or "")
        brand, family = classify_brand(account + " " + action)
        blob = action + " " + link_raw
        plan_ids = [x.upper() for x in re.findall(r"\b(G\d{3,})\b", blob, re.I)]
        meta = plan_meta.get(plan_ids[0]) if plan_ids else None
        if not meta:
            # Match a Posted plan row on the same date when the log omitted the G-id.
            when = parse_when(row.get("date") or "", row.get("time_pt") or "")
            date_s = when.date().isoformat() if when else (row.get("date") or "")
            meta = next((m for m in plan_by_date_link if m.get("date") == date_s), None)
        if meta:
            brand = meta.get("brand") or brand
            family = brand
            if meta.get("id") and meta["id"].upper() not in plan_ids:
                plan_ids.append(meta["id"].upper())
        if not is_own_brand(brand):
            # If the group is in business_or_person, fall back to a Posted USA Wrap row on that date.
            if meta and is_own_brand(meta.get("brand") or ""):
                brand = meta["brand"]
                family = brand
            else:
                continue
        group_name = group_name_from_row(row)
        title = "Group share"
        if meta and meta.get("title"):
            title = "Group share: " + meta["title"]
        else:
            m = re.search(r"group share\s*\(([^)]+)\)", action, re.I)
            if m:
                title = "Group share: " + sanitize(m.group(1))
        when = parse_when(row.get("date") or "", row.get("time_pt") or "")
        for url in urls:
            url = canonical_url(url)
            if url in seen_links:
                continue
            seen_links.add(url)
            pid = plan_ids[0] if plan_ids else None
            posts.append({
                "id": f"group-share:{pid or url}",
                "episode": None,
                "plan_id": pid,
                "group_id": (pid or url).lower(),
                "date": when.date().isoformat() if when else (row.get("date") or ""),
                "time_pt": when.strftime("%-I:%M %p") if when else (row.get("time_pt") or ""),
                "datetime_pt": iso_pt(when) if when else None,
                "brand": brand,
                "family": family or brand,
                "network": network_for(family or brand),
                "platform": "Facebook",
                "title": title,
                "group_name": group_name or None,
                "link": url,
                "kind": "group-share",
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "impressions": None,
                "replies": None,
                "last_updated": iso_pt(when) if when else None,
                "stats_source": "none",
                "stats_note": "Group share stats are n/a unless a public count is available.",
            })
    return posts


def merge_youtube_discoveries(posts: list[dict]) -> list[dict]:
    existing = {canonical_url(p["link"]) for p in posts if p.get("link")}
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

    kind = post.get("kind") or prev.get("kind") or "post"
    if kind == "story":
        # Only keep views/replies that were actually recorded. Do not treat missing as zero.
        views = pick("views")
        replies = pick("replies")
        if replies is None:
            replies = pick("comments")
        if not stats.get("ok") and views is None and replies is None:
            note = "Stories expire after 24 hours. Views and replies are n/a until an in-account check records a real number."
        out = {
            "id": post.get("id") or prev.get("id"),
            "episode": post.get("episode") or prev.get("episode"),
            "plan_id": post.get("plan_id") or prev.get("plan_id"),
            "group_id": post.get("group_id") or prev.get("group_id"),
            "date": post.get("date") or prev.get("date") or "",
            "time_pt": post.get("time_pt") or prev.get("time_pt") or "",
            "datetime_pt": post.get("datetime_pt") or prev.get("datetime_pt"),
            "brand": post.get("brand") or prev.get("brand"),
            "family": post.get("family") or prev.get("family"),
            "network": post.get("network") or prev.get("network") or network_for(post.get("family") or prev.get("family") or ""),
            "platform": post["platform"],
            "title": title or prev.get("title") or "Story",
            "link": post.get("link") or prev.get("link"),
            "kind": "story",
            "expires_at": post.get("expires_at") or prev.get("expires_at"),
            "story_live": post.get("story_live") if post.get("story_live") is not None else prev.get("story_live"),
            "views": views,
            "replies": replies,
            "likes": pick("likes"),
            "comments": replies,
            "shares": None,
            "impressions": None,
            "last_updated": iso_pt(fetched_at) if stats.get("ok") else (prev.get("last_updated") or iso_pt(fetched_at)),
            "stats_source": stats.get("source") if stats.get("ok") else (prev.get("stats_source") or "none"),
            "stats_note": note,
        }
        return out

    if kind == "gbp":
        return {
            "id": post.get("id") or prev.get("id"),
            "plan_id": post.get("plan_id") or prev.get("plan_id"),
            "group_id": post.get("group_id") or prev.get("group_id"),
            "date": post.get("date") or prev.get("date") or "",
            "time_pt": post.get("time_pt") or prev.get("time_pt") or "",
            "datetime_pt": post.get("datetime_pt") or prev.get("datetime_pt"),
            "brand": post.get("brand") or prev.get("brand"),
            "family": post.get("family") or prev.get("family"),
            "network": post.get("network") or prev.get("network") or network_for(post.get("family") or prev.get("family") or ""),
            "platform": "Google Business",
            "location": post.get("location") or prev.get("location"),
            "title": post.get("title") or prev.get("title") or "Google Business post",
            "caption": post.get("caption") or prev.get("caption") or "",
            "link": None,
            "kind": "gbp",
            "status": post.get("status") or prev.get("status") or "Published",
            "views": None,
            "likes": None,
            "comments": None,
            "shares": None,
            "impressions": None,
            "last_updated": iso_pt(fetched_at),
            "stats_source": "none",
            "stats_note": post.get("stats_note") or "Google Business posts have no public URL. Stats are n/a.",
        }

    if kind == "group-share":
        return {
            "id": post.get("id") or prev.get("id") or (canonical_url(post.get("link")) if post.get("link") else None),
            "episode": post.get("episode") or prev.get("episode"),
            "plan_id": post.get("plan_id") or prev.get("plan_id"),
            "group_id": post.get("group_id") or prev.get("group_id"),
            "date": post.get("date") or prev.get("date") or "",
            "time_pt": post.get("time_pt") or prev.get("time_pt") or "",
            "datetime_pt": post.get("datetime_pt") or prev.get("datetime_pt"),
            "brand": post.get("brand") or prev.get("brand"),
            "family": post.get("family") or prev.get("family"),
            "network": post.get("network") or prev.get("network") or network_for(post.get("family") or prev.get("family") or ""),
            "platform": "Facebook",
            "title": post.get("title") or prev.get("title") or "Group share",
            "group_name": post.get("group_name") or prev.get("group_name") or "",
            "link": post.get("link") or prev.get("link"),
            "kind": "group-share",
            "views": pick("views"),
            "likes": pick("likes"),
            "comments": None,
            "shares": None,
            "impressions": None,
            "last_updated": iso_pt(fetched_at),
            "stats_source": "none",
            "stats_note": post.get("stats_note") or "Group share stats are n/a unless a public count is available.",
        }

    out = {
        "id": post.get("id") or prev.get("id") or (canonical_url(post.get("link")) if post.get("link") else None),
        "episode": post.get("episode") or prev.get("episode"),
        "plan_id": post.get("plan_id") or prev.get("plan_id"),
        "group_id": post.get("group_id") or prev.get("group_id"),
        "date": post.get("date") or prev.get("date") or "",
        "time_pt": post.get("time_pt") or prev.get("time_pt") or "",
        "datetime_pt": post.get("datetime_pt") or prev.get("datetime_pt"),
        "brand": post.get("brand") or prev.get("brand"),
        "family": post.get("family") or prev.get("family"),
        "network": post.get("network") or prev.get("network") or network_for(post.get("family") or prev.get("family") or ""),
        "platform": post["platform"],
        "title": title or prev.get("title") or "Untitled post",
        "link": post["link"],
        "kind": "post",
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



def normalize_episode(episode: Optional[str]) -> Optional[str]:
    if not episode:
        return None
    ep = episode.strip().lower()
    ep = re.sub(r"\.(mp4|mov|webm)$", "", ep)
    ep = re.sub(r"-(?:9x16|16x9|1x1|4x5|720p?|1080p?)$", "", ep)
    return ep or None


def title_fingerprint(title: str, family: str) -> str:
    stop = {"the", "a", "an", "to", "in", "of", "and", "for", "on", "at", "plus", "more", "map", "desk", "money", "world", "places", "news"}
    text = re.sub(r"[^a-z0-9\s]+", " ", (title or "").lower())
    words = [w for w in text.split() if w and w not in stop]
    return (re.sub(r"[^a-z0-9]+", "-", (family or "").lower()).strip("-") + ":" + " ".join(words[:5])).strip(":")


def plan_twin_map() -> dict[str, str]:
    """Map a content-plan id to a shared group key for same-day, same-asset twins."""
    path = Path("/workspace/marketing/social/content-plan.csv")
    if not path.exists():
        return {}
    buckets: dict[tuple, list[str]] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            pid = (row.get("id") or "").strip()
            if not pid:
                continue
            preview = (row.get("preview") or "").strip().lower()
            asset = (row.get("asset") or "").strip().lower()
            key = (
                (row.get("date") or "").strip(),
                re.sub(r"\s*\(@[^)]+\)\s*$", "", (row.get("brand") or "")).strip().lower(),
                preview or asset,
            )
            if not key[2]:
                continue
            buckets.setdefault(key, []).append(pid)
    out: dict[str, str] = {}
    for ids in buckets.values():
        if len(ids) < 2:
            # still map singles so plan_id can be the group
            if ids:
                out[ids[0].upper()] = ids[0].lower()
            continue
        group = "-".join(sorted({i.lower() for i in ids}))
        for pid in ids:
            out[pid.upper()] = group
    return out


def keys_from_activity_log() -> dict[str, dict]:
    """Look up episode / plan ids for a published post URL."""
    found: dict[str, dict] = {}
    if not ACTIVITY_LOG.exists():
        return found
    with ACTIVITY_LOG.open() as f:
        for row in csv.DictReader(f):
            action = row.get("action") or ""
            link = row.get("post_link") or ""
            blob = action + " " + link
            episode_m = re.search(r"\b((?:mdm|mdw|mdp)[-a-z0-9]+)\b", blob, re.I)
            plan_ids = [x.upper() for x in re.findall(r"\b((?:H|DW|SJ|MN|X|QVN|G)\d{3,})\b", blob, re.I)]
            if not episode_m and not plan_ids:
                continue
            for url in extract_urls(link, action):
                rec = found.setdefault(canonical_url(url), {"episode": None, "plan_ids": []})
                if episode_m and not rec["episode"]:
                    rec["episode"] = normalize_episode(episode_m.group(1))
                for pid in plan_ids:
                    if pid not in rec["plan_ids"]:
                        rec["plan_ids"].append(pid)
    return found


def assign_group_ids(posts: list[dict]) -> None:
    """Group cross-posts of the same creative (episode, twin plan ids, or title)."""
    url_meta = keys_from_activity_log()
    twins = plan_twin_map()
    for post in posts:
        if post.get("link"):
            meta = url_meta.get(canonical_url(post["link"])) or {}
            if meta.get("episode") and not post.get("episode"):
                post["episode"] = meta["episode"]
            if meta.get("plan_ids"):
                merged = list(dict.fromkeys((post.get("plan_ids") or []) + meta["plan_ids"]))
                post["plan_ids"] = merged
                post["plan_id"] = post.get("plan_id") or (merged[0] if merged else None)
        post["episode"] = normalize_episode(post.get("episode"))
        if post.get("episode"):
            post["group_id"] = post["episode"]
        elif not post.get("group_id"):
            if post.get("plan_id") and post["plan_id"].upper() in twins:
                post["group_id"] = twins[post["plan_id"].upper()]
            elif post.get("plan_ids"):
                mapped = [twins[i] for i in post["plan_ids"] if i.upper() in twins]
                post["group_id"] = mapped[0] if mapped else post["plan_ids"][0].lower()

    # Cluster remaining posts by brand + close dates + similar titles.
    grouped = [p for p in posts if p.get("group_id")]
    ungrouped = [p for p in posts if not p.get("group_id")]
    # Index fingerprints of already grouped posts so discoveries can join them
    fp_groups: dict[str, str] = {}
    for post in grouped:
        fp = title_fingerprint(post.get("title") or "", post.get("family") or post.get("brand") or "")
        if fp:
            fp_groups.setdefault(fp, post["group_id"])

    used: set[int] = set()
    for i, post in enumerate(ungrouped):
        if i in used:
            continue
        fp = title_fingerprint(post.get("title") or "", post.get("family") or post.get("brand") or "")
        if fp in fp_groups:
            post["group_id"] = fp_groups[fp]
            used.add(i)
            continue
        family = post.get("family") or post.get("brand") or ""
        date = post.get("date") or ""
        members = [post]
        idxs = [i]
        for j, other in enumerate(ungrouped):
            if j <= i or j in used:
                continue
            if (other.get("family") or other.get("brand") or "") != family:
                continue
            od = other.get("date") or ""
            if date and od and abs((int(date.replace("-", "") or 0) - int(od.replace("-", "") or 0))) > 1:
                # allow a 1-day window without going wild; compare ISO dates
                try:
                    from datetime import date as _date
                    d1 = _date.fromisoformat(date)
                    d2 = _date.fromisoformat(od)
                    if abs((d1 - d2).days) > 1:
                        continue
                except ValueError:
                    continue
            ofp = title_fingerprint(other.get("title") or "", family)
            if fp and ofp and (fp == ofp or fp.split(":")[-1][:24] == ofp.split(":")[-1][:24] or fp.split(":")[-1].split()[:3] == ofp.split(":")[-1].split()[:3]):
                members.append(other)
                idxs.append(j)
        if post.get("kind") == "story":
            gid = "story:" + slugish(family) + ":" + (date or "undated")
        else:
            gid = slugish((post.get("title") or "post")[:40]) + ":" + (date or "undated")
            if family:
                gid = slugish(family) + ":" + gid
        for m, ix in zip(members, idxs):
            m["group_id"] = gid
            used.add(ix)
            fp_groups.setdefault(title_fingerprint(m.get("title") or "", family), gid)


def slugish(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "item"


def empty_bucket() -> dict:
    return {"posts": 0, "stories": 0, "group_shares": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0, "views_known": 0, "likes_known": 0}


def add_to(bucket: dict, post: dict) -> None:
    if post.get("kind") == "group-share":
        bucket["group_shares"] = bucket.get("group_shares", 0) + 1
        return
    if post.get("kind") == "story":
        bucket["stories"] = bucket.get("stories", 0) + 1
        if post.get("views") is not None:
            bucket["views"] += post["views"]
            bucket["views_known"] += 1
        return
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
        posts = [p for p in posts if not p.get("link") or canonical_url(p["link"]) not in deleted]
        print(f"  excluded {before - len(posts)} deleted post(s)")
    gbp_live, gbp_pending = collect_google_business()
    if gbp_live:
        posts.extend(gbp_live)
        print(f"  added {len(gbp_live)} published Google Business post(s)")
    group_shares = collect_posted_group_shares()
    if group_shares:
        existing = {canonical_url(p["link"]) for p in posts if p.get("link")}
        added = 0
        for share in group_shares:
            if share.get("link") and canonical_url(share["link"]) in existing:
                continue
            if deleted and share.get("link") and canonical_url(share["link"]) in deleted:
                continue
            posts.append(share)
            added += 1
        print(f"  added {added} posted Facebook group share(s)")

    posts = merge_youtube_discoveries(posts)
    for p in posts:
        if not p.get("network"):
            p["network"] = network_for(p.get("family") or "")
        if not p.get("kind"):
            p["kind"] = "post"
    assign_group_ids(posts)
    posts.sort(key=lambda p: p.get("datetime_pt") or p.get("date") or "", reverse=True)

    n_stories = sum(1 for p in posts if p.get("kind") == "story")
    print(f"  {len(posts)} item(s) to score ({n_stories} stor{'y' if n_stories == 1 else 'ies'})")

    x_followers_by_handle = {}
    yt_subscribers = None
    enriched = []
    for post in posts:
        url = post.get("link")
        print(f"  {post.get('kind','post'):6} {post['platform']:10} {url or post.get('id')}")
        stats: dict
        if post.get("kind") in {"gbp", "group-share"}:
            prev_key = post.get("id") or (canonical_url(url) if url else "")
            prev_row = prev_map.get(prev_key) or {}
            stats = {"ok": False, "source": "none"}
            enriched.append(apply_stats(post, stats, prev_row, fetched_at))
            continue
        if post.get("kind") == "story":
            prev_key = post.get("id") or (canonical_url(url) if url else "")
            prev = prev_map.get(prev_key) or {}
            # Keep a previously recorded in-account metric if we have one; do not scrape expired stories.
            recorded = bool(prev.get("views") is not None or prev.get("replies") is not None)
            stats = {
                "ok": recorded,
                "source": prev.get("stats_source") or "none",
                "views": prev.get("views"),
                "replies": prev.get("replies"),
                "likes": prev.get("likes"),
                "comments": prev.get("replies") or prev.get("comments"),
            }
            enriched.append(apply_stats(post, stats, prev, fetched_at))
            continue
        if post["platform"] == "YouTube":
            stats = fetch_youtube(url)
            if stats.get("channel_followers") is not None:
                yt_subscribers = stats["channel_followers"]
        elif post["platform"] == "X":
            stats = fetch_x_post(url)
            handle = (stats.get("handle") or "").lstrip("@").lower()
            if handle and stats.get("channel_followers") is not None:
                x_followers_by_handle[handle] = stats["channel_followers"]
        elif post["platform"] == "Facebook":
            stats = fetch_facebook(url)
        elif post["platform"] == "Instagram":
            stats = fetch_facebook(url)  # yt-dlp also works on many public IG post pages
            if stats.get("source") == "yt-dlp":
                stats["source"] = "yt-dlp"
        else:
            stats = {"ok": False, "source": "none", "note": "Platform not fetched yet"}
        prev = prev_map.get(canonical_url(url) if url else "") or {}
        enriched.append(apply_stats(post, stats, prev, fetched_at))

    # Fill any missing X follower counts from public profiles we already list.
    for handle in ("MapDeskNews", "usawrapco"):
        if handle.lower() not in x_followers_by_handle:
            user = fetch_x_user(handle)
            if user.get("ok") and user.get("followers") is not None:
                x_followers_by_handle[handle.lower()] = user.get("followers")
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
            handle = (ch.get("handle") or "").lstrip("@").lower()
            fol = x_followers_by_handle.get(handle)
            if fol is None:
                fol = next(
                    (
                        c.get("followers")
                        for c in prev.get("channels") or []
                        if c.get("platform") == "X" and (c.get("handle") or "").lstrip("@").lower() == handle
                    ),
                    None,
                )
            entry["followers"] = fol
            entry["followers_available"] = fol is not None
        elif ch["kind"] in {"facebook", "instagram"}:
            entry["followers"] = None
            entry["followers_available"] = False
            entry["note"] = "n/a without a login"
        else:
            entry["followers"] = None
            entry["followers_available"] = False
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
        f"Totals: {totals['posts']} posts, {totals.get('stories', 0)} stories, {totals['views']} views, "
        f"{totals['likes']} likes, YT subs={yt_subscribers}"
    )
    public_scheduled = None
    try:
        from scheduled import write_scheduled_queue
        public_scheduled = write_scheduled_queue(fetched_at, extra_pending=gbp_pending)
    except Exception as exc:
        print(f"Scheduled queue write failed (public dashboard still ok): {exc}")
    if public_scheduled:
        payload["scheduled"] = public_scheduled
        DATA_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
