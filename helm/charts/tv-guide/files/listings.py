"""ErsatzTV XMLTV -> programmes, the grid's rows (60-minute window) and the promo rotation (NOW / NEXT per channel)."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW = timedelta(minutes=60)
GUIDE_CHANNELS = frozenset({1})   # the guide never lists itself


@dataclass(frozen=True)
class Programme:
    channel: int
    channel_name: str
    title: str
    detail: str | None        # episode name for shows, year for movies
    start: datetime
    stop: datetime
    episode: str | None       # "S01E01"
    year: int | None
    poster_url: str | None


@dataclass
class Block:
    title: str
    detail: str | None
    x0: float                 # fractions of the window, 0..1
    x1: float


@dataclass
class Row:
    channel: int
    name: str
    blocks: list


@dataclass
class Promo:
    kind: str                 # 'NOW' | 'NEXT'
    programme: Programme


def _time(s):
    return datetime.strptime(s, '%Y%m%d%H%M%S %z')


def parse_xmltv(xml_text):
    root = ET.fromstring(xml_text)
    channels = {}
    for ch in root.iter('channel'):
        m = re.match(r'(\d+)\s+(.+)', ch.findtext('display-name', ''))
        if m:
            channels[ch.get('id')] = (int(m[1]), m[2])
    progs = []
    for p in root.iter('programme'):
        if p.get('channel') not in channels:
            continue
        number, name = channels[p.get('channel')]
        date = p.findtext('date')
        year = int(date[:4]) if date and date[:4].isdigit() else None
        episode = next((e.text for e in p.findall('episode-num') if e.get('system') == 'onscreen'), None)
        sub = p.findtext('sub-title')
        icon = p.find('icon')
        progs.append(Programme(number, name, p.findtext('title', ''), sub or (str(year) if year else None),
                               _time(p.get('start')), _time(p.get('stop')), episode, year,
                               icon.get('src') if icon is not None else None))
    return progs


def window(now):
    start = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    return start, start + WINDOW


def _by_channel(progs, exclude):
    out = {}
    for p in sorted(progs, key=lambda p: (p.channel, p.start)):
        if p.channel not in exclude:
            out.setdefault(p.channel, []).append(p)
    return out


def rows(progs, now, exclude=GUIDE_CHANNELS):
    w0, w1 = window(now)
    span = (w1 - w0).total_seconds()
    out = []
    for number, ps in _by_channel(progs, exclude).items():
        blocks = [Block(p.title, p.detail,
                        max(0.0, (p.start - w0).total_seconds() / span),
                        min(1.0, (p.stop - w0).total_seconds() / span))
                  for p in ps if p.stop > w0 and p.start < w1]
        out.append(Row(number, ps[0].channel_name, blocks))
    return out


def promos(progs, now, exclude=GUIDE_CHANNELS):
    out = []
    for ps in _by_channel(progs, exclude).values():
        current = next((p for p in ps if p.start <= now < p.stop), None)
        upcoming = next((p for p in ps if p.start >= now), None)
        if current:
            out.append(Promo('NOW', current))
        if upcoming:
            out.append(Promo('NEXT', upcoming))
    return out
