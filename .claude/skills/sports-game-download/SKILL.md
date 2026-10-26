---
name: sports-game-download
description: Download full sports games (NFL first, any league) from YouTube onto the NAS as a TV-show library for ErsatzTV / Jellyfin, with season/episode naming and .nfo metadata. Use this whenever the user shares a YouTube link to a game, a full broadcast or a classic game, or asks to grab, download, save or add a game (Super Bowl, playoff game, "old NFL games", a playlist of games) to the NAS, the sports folder, ErsatzTV or a channel, even if they don't say "skill" or name the folder.
---

# Download sports games to the NAS

Games go on the video share (`/volume2/video`) under `sports/`, in a TV-show layout so ErsatzTV (planned, not deployed
yet) and Jellyfin can scan it as a Shows library:

```
sports/NFL Classic Games/tvshow.nfo
sports/NFL Classic Games/Season 2016/NFL Classic Games - S2016E01 - Super Bowl LI - Patriots vs. Falcons.mp4  (+ .nfo, -thumb.jpg)
```

`sports/` sits outside the Sonarr/Radarr roots, the way `theme-parks/` does. Never touch `personal/`, `iMovie/` or
`youtube/`. `scripts/sports-dl.py` does the mechanical part: download, naming, `.nfo`, skipping duplicates by
YouTube id, and copying. You decide the metadata.

## Steps

1. **Tools** (on the Mac): `yt-dlp` and `ffmpeg` from Homebrew. If yt-dlp fails with extraction or 403 errors, `brew upgrade yt-dlp`
   first: YouTube breaks old versions often.
2. **Mount the share** if `/Volumes/video` is missing: `open smb://192.168.3.20/video` (Finder uses the keychain login).
3. **Identify the game**: `yt-dlp --print "%(title)s | %(upload_date)s | %(duration_string)s" --print description URL`.
   The upload date is NOT the game date (NFL uploads old games years later). Work out from the title, plus the description or your own knowledge:
   - **season**: the year the season started. NFL games in Jan/Feb (playoffs, Super Bowl) belong to the previous
     year: Super Bowl LI (Feb 5, 2017) is season 2016. NBA/NHL 2015-16 is 2015. MLB is the calendar year.
   - **aired**: the real game date, YYYY-MM-DD. Episodes are numbered in download order, so ErsatzTV/Jellyfin
     sort chronologically from this date.
   - **title**: `<Game>: <Away> vs. <Home>` for a named game (`Super Bowl LI: Patriots vs. Falcons`), otherwise
     `Week 12: Cowboys vs. Packers`. A one-line **plot** with the final score is a nice touch, but ask before adding
     spoilers to plots if the user hasn't said.
   - **league**: sets the show, `<League> Classic Games` (default NFL). Use `--show` only if the user wants another name.
   If you're not sure of the date or season, say so and ask; don't guess.
4. **Dry run, then for real** (from the repo root):
   ```
   python3 scripts/sports-dl.py URL --season 2016 --aired 2017-02-05 \
     --title "Super Bowl LI: Patriots vs. Falcons" --plot "Patriots 34, Falcons 28 (OT)." --dry-run
   ```
   Then run it again without `--dry-run`, in the background (a 3-hour broadcast is a few GB). The script prints the target path and episode
   number; `already in library` means that video is already there.
5. **Playlists**: list the entries with `yt-dlp --flat-playlist --print "%(id)s %(title)s" PLAYLIST`, work out metadata
   for each, show the user the table, then run the script once per video (sequentially, not in parallel).

## Notes

- Files written over SMB get the share's DSM permissions, so the `media` user (UID 1029) that Jellyfin/ErsatzTV
  run as can read them. No chown needed.
- Jellyfin has a **Sports** library on `/data/sports` (from `scripts/jellyfin-setup.py`, no online metadata). Its own scan
  runs every 12 hours (no inotify on NFS), so after a download refresh it: Dashboard > Libraries > Sports > Scan, or rerun `jellyfin-setup.sh`.
- When ErsatzTV is deployed: mount `media-data` read-only and add a local **Shows** library on `/data/sports`.
- Personal use only: download what the user asks for, and don't re-share it.
