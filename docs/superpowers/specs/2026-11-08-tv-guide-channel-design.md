# TV Guide channel (Prevue-style) for ErsatzTV

Date: 2026-11-08. Status: design, awaiting approval.

## Intent

ErsatzTV (`argocd/apps/ersatztv`, deployed 2026-11-08) runs retro channels for the household (currently 25 Christmas,
90 90s Rewind; more will come). The user, a millennial building a 90s-TV experience, wants a **TV Guide channel** in the
style of the 1990s **Prevue Channel**: the thing you flip to in order to see what's on. It is a nostalgia piece first; it
should look and sound like Prevue, not like a modern EPG.

## What the viewer sees (decided)

Layout "A · Classic Prevue split" (picked from mockups, `.superpowers/brainstorm/`, `guide-style.html`), 1920x1080:

- **Top-left promo box (~2/3 width, top half):** "NOW ON <CHANNEL>" or "COMING UP ON <CHANNEL>", the title (episode
  name for shows, year for movies) and the channel number, over a **muted 8-10 s clip of that actual title** cut from the
  media file (the user said "you pick"; clips are Claude's call). Rotates through the channels: what's on now, then what's
  next. If a clip can't be cut (file missing, unreadable, odd codec), show the title's poster/backdrop instead.
- **Top-right panel:** big yellow clock (local time, America/New_York) and "DAGAFLIX TV GUIDE".
- **Bottom half:** the Prevue grid scrolling upward continuously: yellow time bar with half-hour columns, one row per
  channel (red/green channel tab with number + name, blue program blocks with titles, episode/year in yellow), roughly
  the next 90 minutes, sliding forward as time passes. Prevue blue/red/yellow palette, condensed bold sans font, black
  1-2px separators, drop shadows on text, as in the mockup.
- **Sound:** background music only (never the clips' audio). EPCOT Main Entrance area music from the user's own library
  (user: "The epcot is good"), read-only from `media-data`:
  - all year: `theme-park-audio/Walt Disney World/EPCOT Main Entrance BGM/Epcot - Main Entrance - Area Music (1982) - Complete Loop Version 1 - HQ (28.30).mp3`
    and `theme-park-audio/Walt Disney World/EPCOT Main Entrance BGM/Epcot - Main Entrance - Area Music (2001) - Complete Loop Version 1 (19.03).mp3`
  - Christmas season (Claude's call: Nov 25 – Dec 25): `theme-park-audio/Walt Disney World/EPCOT Main Entrance BGM/Epcot - Main Entrance - Area Music - Christmas Loop Version 1 (52.12).mp3`
  Music plays continuously across promo changes (no restart every clip).

## Goals

1. A channel **1 "TV Guide"** (Claude's call on number/name) in ErsatzTV, group DAGAFLIX, showing the above, watchable in
   Jellyfin Live TV like the other channels (jim and alicia only; already enforced by `jellyfin-setup.py`).
2. The grid always reflects ErsatzTV's real schedule, and new channels appear without touching the guide.
3. Everything in git (repo conventions, CLAUDE.md), reproducible after a rebuild, with README steps for anything that
   has to be clicked in the ErsatzTV UI.

## Non-goals

- Weather, ads, "local" inserts, a live picture-in-picture of a channel, the clips' audio, listings for the iptv-org
  tuner channels in Jellyfin (only ErsatzTV channels are listed).
- A public route. This is LAN/in-cluster only.

## Anti-goals (would be a failure even if it works)

- Burning CPU/GPU 24/7: it must **only render while someone is watching** the channel, and stop soon after they leave.
- Looking modern/flat. It must read as 90s Prevue.
- Writing to the media share, or touching `personal/`, `iMovie/`, `youtube/` (CLAUDE.md).
- A custom image build: the repo has no image registry or build pipeline (only `.github/workflows/validate.yaml`).
  Use stock images + code/config from git (ConfigMaps).
- Anything internet-facing; ErsatzTV has no login.

## Constraints

- Cluster/repo conventions in `CLAUDE.md`: one folder per app under `argocd/apps/`, or a local chart under
  `helm/charts/<app>` with its own Application (like `helm/charts/glance`, which copies raw files into ConfigMaps);
  pinned versions; sync waves (anything consuming a Vault secret syncs after `secret-stores`); secrets only via
  ExternalSecrets in `helm/charts/secret-stores`; never commit secrets.
- Media: `media-data` PVC (`/data`, read-only), UID 1029 / GID 100 (DSM user `media`), root-squashed NFS.
- GPU: Intel UHD 630 per node, `gpu.intel.com/i915` shares (4 per node; Jellyfin and ErsatzTV each hold one). The
  stock ErsatzTV image's ffmpeg has `vpp_qsv`, `overlay_qsv`, `drawtext` and is proven to work with QSV here; it has
  python3 but no Pillow. Hardware encode is expected (user asked for hardware acceleration).
- ErsatzTV v26.12.0 (legacy) plays **remote streams**: `.yml` definitions (`url` or `script`, `duration`, `is_live`)
  scanned from a local library folder (`RemoteStreamFolderScanner`). Channels/schedules/playouts are UI-only (its API
  only covers FFmpeg profiles and smart collections). The guide's channel uses the FFmpeg profile `1080p h264 QSV`.
- ErsatzTV's XMLTV (`http://ersatztv.media.svc.cluster.local:8409/iptv/xmltv.xml`) has channel, title, sub-title,
  times, but no file paths or artwork links usable for clips. Jellyfin (`http://jellyfin.media.svc.cluster.local:8096`,
  API keys via `scripts/jellyfin-setup.sh` → Vault `secret/jellyfin`) has paths (same `/data` paths) and images.
- Jellyfin 12 accepts only `Authorization: MediaBrowser Token="..."`.

## How it fits together (decided at this level; details are the builder's)

```
viewer (Jellyfin Live TV, ch 1) ─▶ ErsatzTV channel 1 ─▶ remote stream .yml (url, is_live)
                                                            │ on demand
                                                            ▼
                     tv-guide service (media ns): HTTP MPEG-TS stream, renders only while a client is connected
                       ├─ grid/now-next data: ErsatzTV XMLTV (poll ~1/min)
                       ├─ clip paths + poster fallback: Jellyfin API (new key "tv-guide")
                       ├─ clips + EPCOT music: media-data, read-only
                       └─ ffmpeg with QSV (one GPU share)
```

- A new in-cluster **tv-guide** service in namespace `media`, no route, LAN only.
- ErsatzTV gets a git-managed remote-stream definition (e.g. ConfigMap mounted into the ErsatzTV pod) and a local
  library on that folder; channel 1 schedules that stream (Flood).
- The guide excludes itself (channel 1) from its own grid.

## Left to the builder

- Renderer technique: e.g. Python controller drawing the grid/overlays (Pillow or ffmpeg `drawtext`) and ffmpeg
  scrolling/compositing with QSV, vs. headless browser capture. Must meet: 1080p, smooth scroll (~30 fps), faster than
  realtime on the UHD 630 plus one CPU core or so, no custom image.
- Which stock image(s) (the ErsatzTV image is a candidate since its ffmpeg QSV build is proven; Pillow could come from a
  pinned pip install at start or an init container, if used).
- Matching XMLTV programmes to Jellyfin items (title + sub-title/episode, title + year), clip offset choice (avoid
  cold opens/credits, e.g. 20-60% into the file), caching cut clips (emptyDir).
- Stream protocol details (MPEG-TS over HTTP vs HLS), idle-stop timeout, health endpoint, probes, resources.
- Font (condensed bold sans in the spirit of Prevue; must be freely licensed and fetched/pinned reproducibly).
- Uptime Kuma: health monitor via `EXTRA` in `scripts/uptime-kuma-setup.py`, re-run the script (CLAUDE.md).
- README "Media stack plan" item 10 additions; Homepage tile not needed (no UI).

## Verify while building (no author input needed)

- Whether ErsatzTV/Jellyfin keep pulling the stream after the last viewer leaves (decides how idle-stop is detected).
- Whether ErsatzTV's library scan/probe of the remote-stream `.yml` opens the URL (it must not start a long render; a
  probe should get a quick answer or a short render that stops).

## Done when

- Tuning to channel 1 in Jellyfin (as jim) shows layout A with real listings for 25 and 90, a clip in the promo box,
  the clock, scrolling grid and EPCOT music, within ~10 s of tuning in.
- With nobody watching, the tv-guide pod uses ~0 CPU and no ffmpeg runs (checked after the idle timeout).
- `ffmpeg` command lines show QSV in use while streaming.
- Argo apps Synced/Healthy; `make lint` passes; README updated; Uptime Kuma monitor added.
