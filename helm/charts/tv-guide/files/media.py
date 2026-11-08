"""Where a promo's clip comes from: the programme's media file (via Jellyfin, same /data paths) or its poster."""
import hashlib, json, os, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

TIMEOUT = 5
MISS_TTL_S = 3600          # a title Jellyfin hadn't scanned yet gets looked up again after this


def clip_offset(duration_s, slot_seed):
    """A deterministic point 20-60 % into the file: past cold opens and recaps, well before the credits."""
    frac = 0.2 + (int(hashlib.sha256(str(slot_seed).encode()).hexdigest(), 16) % 1000) / 1000 * 0.4
    return round(duration_s * frac, 1)


class Library:
    def __init__(self, base_url, api_key, cache_dir, opener=urllib.request.urlopen, clock=time.time):
        self.base, self.key, self.cache, self.open, self.clock = base_url.rstrip('/'), api_key, Path(cache_dir), opener, clock
        self.cache.mkdir(parents=True, exist_ok=True)
        self.paths_file = self.cache / 'paths.json'
        try:
            self.paths = json.loads(self.paths_file.read_text())
        except (OSError, ValueError):
            self.paths = {}

    def _get(self, path, **query):
        req = urllib.request.Request(f'{self.base}{path}?{urllib.parse.urlencode(query)}',
                                     headers={'Authorization': f'MediaBrowser Token="{self.key}"'})
        with self.open(req, timeout=TIMEOUT) as r:
            return json.load(r)

    def _lookup(self, p):
        if p.episode:
            series = self._get('/Items', IncludeItemTypes='Series', Recursive='true', SearchTerm=p.title)['Items']
            series = [s for s in series if s['Name'] == p.title]
            if not series:
                return None
            for e in self._get(f'/Shows/{series[0]["Id"]}/Episodes', Fields='Path')['Items']:
                if f'S{e.get("ParentIndexNumber") or 0:02d}E{e.get("IndexNumber") or 0:02d}' == p.episode:
                    return e
            return None
        movies = self._get('/Items', IncludeItemTypes='Movie', Recursive='true', SearchTerm=p.title, Fields='Path')['Items']
        return next((m for m in movies if m['Name'] == p.title and (p.year is None or m.get('ProductionYear') == p.year)), None)

    def path_for(self, p, exists=lambda path: os.access(path, os.R_OK)):
        """(path, duration in seconds) of the programme's file, or None. Cached on disk: hits until the file is gone
        (Sonarr/Radarr upgrades rename files), misses for MISS_TTL_S."""
        key = f'{p.title}|{p.episode or p.year}'
        hit = self.paths.get(key)
        stale = (hit is None and key in self.paths) or (isinstance(hit, dict) and self.clock() - hit['miss'] > MISS_TTL_S) \
            or (isinstance(hit, list) and not exists(hit[0]))
        if key not in self.paths or stale:
            try:
                item = self._lookup(p)
            except Exception:
                return None                   # Jellyfin down: don't cache, try again next item
            self.paths[key] = [item['Path'], item['RunTimeTicks'] / 1e7] if item and item.get('Path') and item.get('RunTimeTicks') \
                else {'miss': self.clock()}
            _write_atomic(self.paths_file, json.dumps(self.paths).encode())
        hit = self.paths[key]
        return (hit[0], hit[1]) if isinstance(hit, list) else None

    def poster_for(self, p):
        if not p.poster_url:
            return None
        f = self.cache / 'posters' / (hashlib.sha256(p.poster_url.encode()).hexdigest()[:24] + '.img')
        if f.exists():
            return str(f)
        try:
            with self.open(urllib.request.Request(p.poster_url), timeout=TIMEOUT) as r:
                data = r.read()
            _write_atomic(f, data)
            return str(f) if f.exists() else None
        except Exception:
            return None


def _write_atomic(path, data):
    """Concurrent renders (two viewers, a probe during playback) must never read a half-written file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        pass
