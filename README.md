# Video News Network dashboard

A public, static dashboard of every live post from **Map Desk** (Money, World, Places, Weather, and Battle), **Harbor News**, **Quint Valley News**, **Big Brave Rambo**, **USA Wrap Co**, **DekWave**, **Savajay**, and **Chance the Wrapper**, with the engagement each post has gathered.

Live site: https://usawrapco-spec.github.io/network-dashboard/

This is a GitHub Pages site. There is no build step and no paid host. The page is a dark, mobile-friendly analytics view.

## What it shows

The page opens on a Posts tab (one row per creative, with per-platform stats side by side). Platform tabs follow for YouTube, X, Facebook, Instagram, and Google Business, plus a Scheduled tab for upcoming work.

- Brand groups (All / News / Local businesses) sit above a selector for each brand. Map Desk has sub-filters for Money, World, and Places. Map Battle is its own brand. Each brand view has its own platform breakdown, Posts list, Stories, and Scheduled list.
- The Accounts tab opens with a news-brand gap matrix (YouTube, X, Instagram, Facebook, TikTok, Threads), then lists each brand's public channels. Status is Live, Needs fix, Not set up, or Skipped. Edit `accounts.json` to add or change accounts. Never put emails, passwords, vault ids, phones, or login names in that file.
- Map Desk World, Money, Weather, and Battle post on the main Map Desk accounts, with YouTube playlists. Map Desk Places has its own YouTube channel (`@MapDeskPlaces`) so it can be compared with Money; Places X posts still go out on `@MapDeskNews`. Map Desk and Places YouTube channels share one Google account. Do not create `youtube.com/@MapBattle` — that handle is used by another creator.
- TikTok posting is skipped. Facebook posting is held while the Facebook session is signed out.
- Platform cards at the top show posts, views or impressions, likes or reactions, and followers or subscribers. Google Business cards show published vs pending-review counts, because those posts have no public URL.
- Inside each social channel: account totals per brand, then that channel's posts, newest first.
- Instagram reads "Coming soon" until there are live posts.
- Facebook also lists Stories and posted group shares.
- Google Business rows show date, location (business name only), caption, and status. Published posts are in the live views; Pending review posts are on Scheduled. Skipped posts are left off.
- A growth chart at the bottom is built from timestamped snapshots in `stats/`.

Fields a platform does not expose are shown as **n/a**, not as zero.

## How stats are collected

| Platform | What is real | What may be n/a |
| --- | --- | --- |
| YouTube | Views, likes, subscriber count via `yt-dlp` | Comments, if YouTube does not expose them |
| X | Impressions/views, likes, reposts, replies, followers via public FixTweet / syndication APIs | Official organic metrics (those need account-owner auth) |
| Facebook | Reel view counts via `yt-dlp` on the public video page | Likes, comments, shares, photo posts, permalink posts, Stories, group shares, and page followers — Facebook hides these without a login |
| Instagram | Feed views and likes via `yt-dlp` when the public page exposes them | Story views and replies unless an in-account check recorded a real number |
| Google Business | Date, location, caption, and status from the public calendar / activity log | Views and a public URL (Google Business posts have no public link) |

Deleted posts and skipped rows are left off. The public page never includes phone numbers, personal last names, emails, screenshot paths, or `/workspace` paths.

## Refresh (on the box)

```bash
/workspace/marketing/social/video-news-network/dashboard/publish.sh
```

That script runs `collect.py` (which reads `/workspace/marketing/social/activity-log.csv` and `content-plan.csv` read-only, writes `data.json` and `stats/YYYY-MM-DD-HH.json`) and pushes the update to `main`. GitHub Pages then serves the new numbers.

To collect without publishing:

```bash
python3 /workspace/marketing/social/video-news-network/dashboard/collect.py
```

`collect.py` also writes an allowlisted `scheduled.json` plus stills and small 720p preview videos under `media/scheduled/`. Scheduled lists ready, approved, or pending Google review posts. Unpublished drafts with phones, owner fields, or internal notes stay in the gitignored `private/` folder and are not published.
