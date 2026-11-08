# TV Guide Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ErsatzTV channel 1 "TV Guide": a Prevue-style split screen (promo clip + clock on top, scrolling grid below,
EPCOT music) rendered on demand from ErsatzTV's real schedule.

**Architecture:** A stdlib-only Python renderer (in a local chart's ConfigMaps, mounted into the ErsatzTV pod) builds an
ASS subtitle file (boxes, text, scrolling grid, clock) plus one ffmpeg command (promo clips + music + `ass` filter →
`h264_qsv` MPEG-TS on stdout). ErsatzTV runs it as a `script:` remote stream (`is_live`, 60 s per item, Flood), so it
only runs while someone watches and is killed when they leave. State that must be continuous across 60 s items (scroll
position, promo rotation, music offset) is derived from wall-clock time, not stored.

**Tech Stack:** Python 3.12 stdlib (in the `ersatztv/legacy:v26.12.0` image), ffmpeg 8 (libass, QSV) from the same
image, Helm local chart, Argo CD, Barlow Condensed (OFL).

**Spec:** `docs/superpowers/specs/2026-11-08-tv-guide-channel-design.md`

**Deviations from the spec (Claude's calls, tell the user):**
- No separate `tv-guide` Deployment/Service: ErsatzTV runs the renderer as a remote-stream `script:` inside its own pod
  (on demand by construction, killed on disconnect, reuses ErsatzTV's GPU share). So no Uptime Kuma monitor (no
  endpoint) and no new Jellyfin key: the renderer reads ErsatzTV's own `/config/jellyfin-secrets.json` (read-only).
- Poster fallback uses the poster URLs already in ErsatzTV's XMLTV (`<icon src>`), not Jellyfin images.

## Global Constraints

- Image: no custom builds; code + fonts + stream definition come from ConfigMaps of local chart `helm/charts/tv-guide`.
- Python: stdlib only (no Pillow, no pip). Runs as UID 1029 in the ErsatzTV pod.
- Output: 1920x1080, 30 fps, H.264 via `h264_qsv`, AAC stereo 48 kHz, MPEG-TS on stdout, exactly `--seconds` long.
- Layout (px): promo box `0,0 1280x540`; side panel `1280,0 640x540`; grid `0,540 1920x540` (time bar 54 high, rows
  108 high, channel tab 420 wide, 3 half-hour columns of 500 px). Colours (ASS `&HBBGGRR&`): grid/side bg `#06106B`,
  program block `#2E48C9`, channel tab red `#C21B1B`/green `#1F8A2B` alternating, yellow `#FFE14D`, time bar `#C9B200`,
  separators black.
- Copy: side panel "DAGAFLIX" / "TV GUIDE"; promo "NOW ON <NAME>" / "COMING UP ON <NAME>" (upper case), then title,
  then detail (episode name for shows, year for movies), then "CHANNEL <n>". Clock `h:MM AM/PM`, America/New_York.
- Grid: window = now floored to :00/:30, 90 minutes; channels sorted by number; channel 1 (the guide) excluded.
- Promo: 10 s each; rotation order = for each channel (ascending): NOW, NEXT. Clip offset 20-60 % into the file.
- Music (read-only, `/data/theme-park-audio/Walt Disney World/EPCOT Main Entrance BGM/`):
  `Epcot - Main Entrance - Area Music (1982) - Complete Loop Version 1 - HQ (28.30).mp3` (1710 s),
  `Epcot - Main Entrance - Area Music (2001) - Complete Loop Version 1 (19.03).mp3` (1143 s); Nov 25 – Dec 25 instead
  `Epcot - Main Entrance - Area Music - Christmas Loop Version 1 (52.12).mp3` (3132 s). Never the clips' audio.
- Media is read-only; never write under `/data`. Cache (paths, posters, ASS) lives in `/transcode/tv-guide/` (emptyDir).
- Repo conventions (CLAUDE.md): pinned versions, `make lint` passes, never commit secrets; commit only this work (the
  working tree has unrelated uncommitted edits: stage files explicitly, and for `README.md` stage only item 10's hunk).

## Review Focus

1. A title containing `{`, `}` or `\` (or 80+ chars) must render literally/ellipsized, not break the ASS file → test in Task 2.
2. A programme that started before the window or ends after it, a gap, or a channel with no NEXT → blocks clipped to the window, promo list skips missing NEXT → tests in Task 1.
3. A media file missing/unreadable when rendering (NAS hiccup, item deleted) → that promo falls back to the poster, then to a plain blue box → test in Task 3.
4. XMLTV or Jellyfin unreachable → still a full 60 s stream: blank promos, grid text "LISTINGS UNAVAILABLE", music → test in Task 4.
5. One channel or 12 channels → grid still scrolls continuously with no visible jump between 60 s items (rows repeated to fill; offset from wall clock) → test in Task 2.

---

### Task 1: Chart scaffold, fonts, XMLTV listings model

**Files:**
- Create: `helm/charts/tv-guide/Chart.yaml` (`name: tv-guide`, `version: 0.1.0`), `helm/charts/tv-guide/values.yaml` (empty map with a header comment), `helm/charts/tv-guide/.helmignore` (`tests/`, `__pycache__/`)
- Create: `helm/charts/tv-guide/fonts/BarlowCondensed-{SemiBold,Bold,ExtraBold}.ttf`, `fonts/OFL.txt` from `https://raw.githubusercontent.com/google/fonts/60824dce48f7dd28fe7d65559f2da1f6e04e585b/ofl/barlowcondensed/`
- Create: `helm/charts/tv-guide/files/listings.py`
- Test: `helm/charts/tv-guide/tests/test_listings.py`, fixture `helm/charts/tv-guide/tests/xmltv.xml` (copy of the live feed's shape: channels 1 TV Guide, 25 Christmas, 90 90s Rewind; include a movie with `<date>`, an episode with `<sub-title>` + `<episode-num system="onscreen">`, a programme straddling the window start, a gap)

**Interfaces:**
- Produces (all in `listings.py`):
  - `@dataclass(frozen=True) Programme: channel: int, channel_name: str, title: str, detail: str | None, start: datetime, stop: datetime, episode: str | None, year: int | None, poster_url: str | None` (`detail` = sub-title for episodes, `str(year)` for movies)
  - `parse_xmltv(xml_text: str) -> list[Programme]` (channel number/name from the first `<display-name>` "25 Christmas")
  - `window(now: datetime) -> tuple[datetime, datetime]`
  - `@dataclass Block: title: str, detail: str | None, x0: float, x1: float` (fractions 0..1 of the window)
  - `@dataclass Row: channel: int, name: str, blocks: list[Block]`
  - `rows(progs: list[Programme], now: datetime, exclude: frozenset[int] = frozenset({1})) -> list[Row]`
  - `@dataclass Promo: kind: str ('NOW'|'NEXT'), programme: Programme`
  - `promos(progs, now, exclude=frozenset({1})) -> list[Promo]`

- [ ] **Step 1: Write the failing tests** (`unittest`, load module by path like `scripts/tests/test_lab_power.py`)
  - `test_parse_channels_and_fields`: 3 channels parsed; Christmas movie has `year == 1997`, `detail == '1997'`, `poster_url` set; Simpsons episode has `detail == 'Simpsons Roasting on an Open Fire'`, `episode == 'S01E01'`.
  - `test_window_floors_to_half_hour`: 15:44 → (15:30, 17:00); 16:00 → (16:00, 17:30).
  - `test_rows_exclude_guide_and_sort`: channel numbers `[25, 90]`.
  - `test_blocks_clipped_to_window` (Review Focus 2): straddling programme gets `x0 == 0.0`; one ending after window gets `x1 == 1.0`; programmes fully outside are dropped; a gap leaves no block.
  - `test_promos_now_then_next`: `[(25,'NOW'),(25,'NEXT'),(90,'NOW'),(90,'NEXT')]`; a channel whose current programme is the last in the feed yields only NOW (Review Focus 2).

- [ ] **Step 2: Run** `python3 -m unittest discover -s helm/charts/tv-guide/tests -v` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement `listings.py`** with `xml.etree.ElementTree`; times `"%Y%m%d%H%M%S %z"`; all datetimes tz-aware.
- [ ] **Step 4: Run tests** — Expected: PASS.
- [ ] **Step 5: Commit** `git add helm/charts/tv-guide` → `TV guide: chart scaffold, fonts, XMLTV listings model`

### Task 2: ASS screen builder

**Files:**
- Create: `helm/charts/tv-guide/files/screen.py`
- Test: `helm/charts/tv-guide/tests/test_screen.py`

**Interfaces:**
- Consumes: `Row`, `Promo` from Task 1.
- Produces: `build_ass(rows: list[Row], promos: list[Promo], win: tuple[datetime, datetime], start: datetime, seconds: int, error: str | None = None) -> str` — one ASS document (PlayResX 1920, PlayResY 1080, font "Barlow Condensed") covering `seconds` from `start`; promos are shown 10 s each in the given order starting with `promos[int(start.timestamp()) // 10 % len(promos)]`; `error` replaces the grid rows with that text. Also `SCROLL_PX_PER_S = 40`, `escape(text: str, max_len: int) -> str`.

- [ ] **Step 1: Write the failing tests**
  - `test_header_and_size`: contains `PlayResX: 1920`, `PlayResY: 1080`, `Barlow Condensed`.
  - `test_clock_one_event_per_second`: `seconds=60` → exactly 60 clock events; at 15:44:00 New York the first reads `3:44 PM`.
  - `test_promo_text_and_rotation`: start epoch with `// 10 % 4 == 2` → first promo text `NOW ON 90S REWIND`; 6 promo slots in 60 s.
  - `test_escape_special_and_long` (Review Focus 1): `escape('A {b} \\c', 80)` has no `{` or `}` left (libass would read them as override blocks; replace with `(`/`)`) and the backslash still shows as a character (libass escapes it as `\\`); a 100-char title ends with `…` and is ≤ 80 chars.
  - `test_scroll_continuous_across_items` (Review Focus 5): grid row y at end of item A (start t, 60 s) equals y at start of item B (start t+60) for 1 channel and for 12 channels.
  - `test_error_text`: `error='LISTINGS UNAVAILABLE'` → present, no row events.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement `screen.py`**: static panels as `\p1` vector boxes; each grid row a Dialogue with `\move` from y to y−`SCROLL_PX_PER_S*seconds`, rows repeated until the visible area + travel is covered; base offset `(start.timestamp() * SCROLL_PX_PER_S) % (len(rows) * 108)`; time bar drawn on top (higher layer) so rows scroll under it; clip rows to the grid area with `\clip(0,594,1920,1080)`; text with 2 px black shadow (`\shad2`).
- [ ] **Step 4: Run tests** — Expected: PASS.
- [ ] **Step 5: Commit** → `TV guide: ASS screen builder (Prevue layout, scrolling grid, clock)`

### Task 3: Clip/poster lookup and the ffmpeg command

**Files:**
- Create: `helm/charts/tv-guide/files/media.py`, `helm/charts/tv-guide/files/pipeline.py`
- Test: `helm/charts/tv-guide/tests/test_media.py`, `helm/charts/tv-guide/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Programme`, `Promo` (Task 1).
- Produces:
  - `media.py`: `class Library(base_url: str, api_key: str, cache_dir: Path, opener=urllib.request.urlopen)` with `path_for(p: Programme) -> str | None` (movie: `/Items?IncludeItemTypes=Movie&Recursive=true&SearchTerm=<title>&Fields=Path` filtered by `ProductionYear == year`; episode: series by name, then `/Shows/{id}/Episodes?Fields=Path` matching `S{ParentIndexNumber:02}E{IndexNumber:02}`; results cached in `cache_dir/paths.json` incl. misses; auth header `MediaBrowser Token="<key>"`; any error → `None`) and `poster_for(p: Programme) -> str | None` (download `poster_url` to `cache_dir/posters/`, reuse if present; error → `None`). `clip_offset(duration_s: float, slot_seed: int) -> float` in `[0.2, 0.6] * duration_s`, deterministic.
  - `pipeline.py`: `@dataclass Segment: kind: str ('clip'|'image'|'blank'), path: str | None, offset: float` (each 10 s); `music_for(now: datetime) -> tuple[str, float]` (file, offset = epoch mod file length, constants from Global Constraints); `ffmpeg_cmd(segments: list[Segment], music: tuple[str, float], ass_path: str, fonts_dir: str, seconds: int) -> list[str]`.

- [ ] **Step 1: Write the failing tests**
  - `test_movie_and_episode_lookup`: fake opener returning canned JSON → movie path for "Home Alone 3"/1997; episode path for Simpsons S01E01; second call served from cache (opener not called again).
  - `test_lookup_miss_and_error`: no match → `None`; opener raising `URLError` → `None`.
  - `test_clip_offset_range`: for durations 1320 s and 6000 s and seeds 0..50, offset within 20-60 %.
  - `test_music_season`: Dec 1 → Christmas file; Nov 24 and Dec 26 → a year-round file; offset < that file's length.
  - `test_cmd_core`: contains `-init_hw_device qsv=hw`, `h264_qsv`, `-t 60`, `-f mpegts`, `pipe:1`, `ass=` with `fontsdir=`, the music path with `-stream_loop -1`; clip inputs use `-ss <offset> -t 10`; image inputs use `-loop 1 -t 10`; blank uses a lavfi `color` source; no clip audio mapped (`-map` only the filter's video and the music input's audio).
  - `test_missing_file_falls_back` (Review Focus 3): `segments_for(promos, library, now)` (also in `pipeline.py`, `-> list[Segment]`) returns `image` when `path_for` returns a path that `os.access(path, os.R_OK)` rejects and a poster exists, `blank` when neither.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement `media.py` and `pipeline.py`.** Filter graph: each segment → `scale=1280:540:force_original_aspect_ratio=decrease,pad=1280:540:-1:-1:color=black,setsar=1,fps=30,format=yuv420p` → `concat=n=<k>:v=1:a=0` → overlay on `color=c=0x06106B:s=1920x1080:r=30` (ffmpeg colours are 0xRRGGBB) at 0:0 → `ass=<file>:fontsdir=<dir>` → `format=nv12,hwupload=extra_hw_frames=64` → `h264_qsv -b:v 8M -maxrate 8M -bufsize 16M -g 60`; audio `aac -b:a 192k -ar 48000 -ac 2`.
- [ ] **Step 4: Run tests** — Expected: PASS.
- [ ] **Step 5: Commit** → `TV guide: clip/poster lookup and ffmpeg pipeline`

### Task 4: Entry point, ConfigMaps, render on the cluster

**Files:**
- Create: `helm/charts/tv-guide/files/render.py`, `helm/charts/tv-guide/streams/tv-guide.yml`, `helm/charts/tv-guide/templates/configmaps.yaml`
- Test: `helm/charts/tv-guide/tests/test_render.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `render.py --seconds N` (default 60) writes the stream to stdout and exits 0. `plan(now: datetime, xmltv: str | None, library) -> tuple[str, list[str]]` (ASS text, ffmpeg argv) so it is testable without running ffmpeg. Constants: XMLTV `http://127.0.0.1:8409/iptv/xmltv.xml`, secrets `/config/jellyfin-secrets.json`, cache `/transcode/tv-guide`, fonts `/tv-guide/fonts`. `main` writes the ASS to the cache dir then `os.execvp('ffmpeg', argv)`.
- ConfigMaps (namespace `media`): `tv-guide-app` (`files/*.py`), `tv-guide-streams` (`streams/*.yml`), `tv-guide-fonts` (`binaryData` from `fonts/*.ttf`). Stream definition:
  ```yaml
  title: TV Guide
  script: python3 /tv-guide/app/render.py --seconds 60
  duration: "00:01:00"
  is_live: true
  ```

- [ ] **Step 1: Write the failing test** `test_plan_without_listings` (Review Focus 4): `plan(now, None, library_that_raises)` → ASS contains `LISTINGS UNAVAILABLE`, argv has 6 `blank` segments and still `-t 60`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement `render.py`** (each fetch has a 5 s timeout; any exception → the fallback path, never a non-zero exit before ffmpeg) **and `templates/configmaps.yaml`** (`.Files.Glob ... AsConfig` for text, `range` + `b64enc` for `binaryData`).
- [ ] **Step 4: Run** all tests + `make lint` + `helm template tv-guide helm/charts/tv-guide | grep -c 'kind: ConfigMap'` — Expected: PASS, lint ok, `3`.
- [ ] **Step 5: Render on the cluster before deploying.** Copy `files/` and `fonts/` into the ErsatzTV pod (`kubectl -n media cp` to `/transcode/tvg-test/`), run with `--seconds 20` (paths overridable via env `TVG_APP_DIR`/`TVG_FONTS_DIR` for this test only) to `/transcode/tvg-test/out.ts`. Expected: ffmpeg `speed=` ≥ 1.5x, `ffprobe` shows h264 1920x1080 + aac. Extract frames at 2 s and 15 s (`ffmpeg -ss 2 -i out.ts -frames:v 1 f.png`), copy to `.playwright-mcp/`, look at them: layout A, readable grid, clip in the promo box, clock. Fix and repeat until it looks like the mockup (`.superpowers/brainstorm/*/content/guide-style.html`). Delete `/transcode/tvg-test` afterwards.
- [ ] **Step 6: Commit** → `TV guide: render entry point and ConfigMaps`

### Task 5: Deploy and wire into ErsatzTV + Jellyfin

**Files:**
- Create: `argocd/apps/tv-guide/application.yaml` (local chart `helm/charts/tv-guide`, namespace `media`, sync-wave `"-1"`, standard sync policy/retry, like `argocd/apps/glance/application.yaml`)
- Modify: `argocd/apps/ersatztv/values.yaml` — `persistence` entries (type `configMap`, read-only): `tv-guide-app` → `/tv-guide/app`, `tv-guide-streams` → `/tv-guide/streams`, `tv-guide-fonts` → `/tv-guide/fonts`; header comment line about the guide.
- Modify: `README.md` item 10 — TV Guide paragraph (what it is, where the code lives, the UI steps below, music files, how to change it).

- [ ] **Step 1: Commit + push** (explicit paths only), wait for Argo `tv-guide` and `ersatztv` Synced/Healthy; `kubectl -n media exec deploy/ersatztv -- ls /tv-guide/app /tv-guide/streams /tv-guide/fonts` lists the files.
- [ ] **Step 2: ErsatzTV UI** (Playwright via `kubectl -n media port-forward svc/ersatztv 18409:8409`): Media Sources → Local → "Remote Streams" library → add path `/tv-guide/streams`, save, scan. Check whether the scan executed the script (`ps` in the pod during the scan, logs): acceptable if it only ran briefly.
- [ ] **Step 3: Smart collection** via API (`POST /api/collections/smart/new`, key from `/config/api-secrets.json`, called inside the pod): name `TV Guide`; find the query that returns exactly that item in the UI search (try `type:remote_stream`, else `title:"TV Guide"`).
- [ ] **Step 4: Channel** 1 "TV Guide", group DAGAFLIX, FFmpeg profile `1080p h264 QSV`; schedule "TV Guide": one item, Smart Collection `TV Guide`, Chronological, Flood; playout channel 1 ↔ schedule. Then Jellyfin guide refresh (`RefreshGuide` scheduled task).
- [ ] **Step 5: Verify Done-when** (spec):
  - `curl -m 30 http://127.0.0.1:18409/iptv/channel/1.ts -o /private/tmp/g.ts` → ffprobe h264 1920x1080 + aac; extract a frame and look at it.
  - While streaming: `ps` in the pod shows `render.py`/ffmpeg with `h264_qsv`; time-to-first-byte < 10 s.
  - 2 minutes after stopping: no `render.py`/guide ffmpeg processes in the pod.
  - Jellyfin (as jim) lists channel 1 "TV Guide" with programme data. Ask the user to tune in on a TV.
- [ ] **Step 6: Commit + push README** (stage only item 10's hunk), plus `.superpowers/` in `.gitignore`.
