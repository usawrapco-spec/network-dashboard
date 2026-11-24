# Video News Network dashboard

A public, static dashboard of every live post from **Map Desk** (Money, World, and Places), **Harbor News**, and **Quint Valley News**, with the engagement each post has gathered.

Live site: https://usawrapco-spec.github.io/network-dashboard/

This is a GitHub Pages site. There is no build step and no paid host. The page is a dark, mobile-friendly analytics view and is set up so more brands can be added later from the same public-post data file.

## What it shows

The page is organized by social channel. A row of platform cards at the top (YouTube, X, Facebook, Instagram) shows posts, views or impressions, likes or reactions, and followers or subscribers, each with the platform name and an icon. Click a card or a tab to open that channel.

- Inside each channel: account totals per brand (Map Desk @MapDeskNews, Harbor News, Quint Valley News), then that channel's posts, newest first
- Columns fit the platform: YouTube shows views and likes; X shows impressions, likes, reposts, and replies; Facebook shows views and reactions
- Instagram reads "Coming soon" until there are live posts
- A growth chart at the bottom is built from timestamped snapshots in `stats/`

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

The public site has a Posts tab (one row per creative, with per-platform stats), per-channel tabs, and a Scheduled tab for upcoming posts that have passed QA or are approved. collect.py writes an allowlisted `scheduled.json` plus stills and small 720p preview videos under `media/scheduled/`. Unpublished drafts with phones, owner fields, or internal notes stay in the gitignored `private/` folder and are not published.
