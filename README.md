# Video News Network dashboard

A public, static dashboard of every live post from **Map Desk** (Money, World, and Places), **Harbor News**, and **Quint Valley News**, with the engagement each post has gathered.

Live site: https://usawrapco-spec.github.io/network-dashboard/

This is a GitHub Pages site. There is no build step and no paid host.

## What it shows

- Totals by brand and by platform (YouTube, X, Facebook)
- Channel-level YouTube subscribers and X followers
- A sortable table of every post: date, brand, platform, title, link, views, likes, comments, shares, last updated
- A growth chart from timestamped snapshots in `stats/`

Fields the platform does not expose are shown as **n/a**, not as zero.

## How stats are collected

| Platform | What is real | What may be n/a |
| --- | --- | --- |
| YouTube | Views, likes, subscriber count via `yt-dlp` | Comments, if YouTube does not expose them |
| X | Impressions/views, likes, reposts, replies, followers via public FixTweet / syndication APIs | Official organic metrics (those need account-owner auth) |
| Facebook | Reel view counts via `yt-dlp` on the public video page | Likes, comments, shares, photo posts, permalink posts, and page followers — Facebook hides these without a login |

USA Wrap Co, DekWave, and Savajay posts are out of scope. Deleted posts and log rows with no post URL are skipped. The public page never includes phone numbers or personal names.

## Refresh (on the box)

```bash
/workspace/marketing/social/video-news-network/dashboard/publish.sh
```

That script runs `collect.py` (which reads `/workspace/marketing/social/activity-log.csv`, writes `data.json` and `stats/YYYY-MM-DD-HH.json`) and pushes the update to `main`. GitHub Pages then serves the new numbers.

To collect without publishing:

```bash
python3 /workspace/marketing/social/video-news-network/dashboard/collect.py
```
