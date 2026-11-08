"""The Prevue screen as three ASS documents, drawn by ffmpeg's `ass` filter (libass):

- frame_ass: everything that never moves (side panel, grid background, time bar) -> frame.png, once per item
- strip_ass: the grid rows, repeated into one tall image -> strip.png; ffmpeg scrolls it with a moving crop
- overlay_ass: the only live text (clock, promo captions), over the promo clips

Drawing the moving rows with libass on every frame ran slower than realtime; stills + crop are nearly free. The
scroll offset and promo rotation come from wall-clock time so the 60-second items ErsatzTV chains together line up.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/New_York')
W, H = 1920, 1080
PROMO_W, TOP_H = 1280, 540                 # promo box 0,0 1280x540; side panel 1280,0 640x540
GRID_TOP = 594                             # time bar 540..594, rows scroll below it
GRID_VISIBLE = H - GRID_TOP
ROW_H, TAB_W, COL_W = 108, 420, 500        # 3 half-hour columns of 500 px
MIN_TEXT_W = 100                           # narrower blocks get no title (it would spill into the next block)
SCROLL_PX_PER_S = 40
PROMO_S = 10
FONT = 'Barlow Condensed'

SIDE, BLOCK = '#0A1A6B', '#2E48C9'
RED, GREEN, YELLOW, GOLD, BLACK, WHITE = '#C21B1B', '#1F8A2B', '#FFE14D', '#C9B200', '#000000', '#FFFFFF'


def colour(hex_rgb):
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f'&H{b}{g}{r}&'


def escape(text, max_len):
    text = text.replace('{', '(').replace('}', ')')
    for c in 'Nnh':                         # libass escapes; any other backslash renders as itself
        text = text.replace('\\' + c, '\\⁠' + c)
    if len(text) > max_len:
        text = text[:max_len - 1].rstrip() + '…'
    return text


def _ts(seconds):
    cs = int(round(seconds * 100))
    return f'{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}'


def _event(start, end, name, text, layer=0):
    return f'Dialogue: {layer},{_ts(start)},{_ts(end)},G,{name},0,0,0,,{text}'


def _box(x, y, w, h, fill, extra=''):
    return f'{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c{colour(fill)}{extra}\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}'


def _text(x, y, size, fill, text, an=7, extra=''):
    return f'{{\\an{an}\\pos({x},{y})\\fs{size}\\b1\\1c{colour(fill)}\\shad3\\4c&H000000&\\bord0{extra}}}{text}'


def _clock(t):
    return t.astimezone(TZ).strftime('%I:%M %p').lstrip('0')


def _doc(height, events):
    return '\n'.join([
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {W}', f'PlayResY: {height}', 'WrapStyle: 2',
        'ScaledBorderAndShadow: yes', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
        'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, '
        'MarginR, MarginV, Encoding',
        f'Style: G,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,2,7,0,0,0,1', '',
        '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text', *events, ''])


def scroll_offset(cycle, start, t):
    """Pixels the grid strip has scrolled at `t` seconds into the item that starts at `start` (0 <= offset < cycle)."""
    return round((start.timestamp() + t) * SCROLL_PX_PER_S % cycle, 3)


def frame_ass(win):
    ev = [
        _event(0, 1, 'frame', _box(PROMO_W, 0, W - PROMO_W, TOP_H, SIDE)),
        _event(0, 1, 'frame', _box(PROMO_W, 0, 6, TOP_H, GOLD), 1),
        _event(0, 1, 'frame', _text(PROMO_W + 320, 330, 64, WHITE, 'DAGAFLIX', an=5), 1),
        _event(0, 1, 'frame', _text(PROMO_W + 320, 400, 64, YELLOW, 'TV GUIDE', an=5), 1),
        _event(0, 1, 'frame', _box(0, TOP_H, W, GRID_TOP - TOP_H, GOLD), 1),
        _event(0, 1, 'frame', _box(0, TOP_H, W, 4, BLACK), 2),
        _event(0, 1, 'frame', _text(20, TOP_H + 27, 40, BLACK, win[0].astimezone(TZ).strftime('%a %b %-d').upper(),
                                    an=4, extra='\\shad0'), 2),
    ]
    for i in range(3):
        x = TAB_W + i * COL_W
        ev.append(_event(0, 1, 'frame', _box(x, TOP_H, 3, GRID_TOP - TOP_H, BLACK), 2))
        ev.append(_event(0, 1, 'frame', _text(x + 16, TOP_H + 27, 40, BLACK, _clock(win[0] + timedelta(minutes=30 * i)),
                                              an=4, extra='\\shad0'), 2))
    return _doc(H, ev)


def _row(row, idx, y):
    tab = RED if idx % 2 == 0 else GREEN
    ev = [
        _event(0, 1, 'row', _box(0, y + 2, TAB_W - 3, ROW_H - 4, tab)),
        _event(0, 1, 'row', _text(20, y + 8, 56, WHITE, str(row.channel)), 1),
        _event(0, 1, 'row', _text(20, y + 58, 40, YELLOW, escape(row.name.upper(), 18)), 1),
    ]
    for b in row.blocks:
        x0 = TAB_W + round(b.x0 * 3 * COL_W)
        w = TAB_W + round(b.x1 * 3 * COL_W) - x0 - 3
        if w < 4:
            continue
        ev.append(_event(0, 1, 'row', _box(x0, y + 2, w, ROW_H - 4, BLOCK)))
        if w < MIN_TEXT_W:
            continue
        ev.append(_event(0, 1, 'row', _text(x0 + 14, y + 10, 46, WHITE, escape(b.title, max(3, int(w / 19)))), 1))
        if b.detail:
            ev.append(_event(0, 1, 'row', _text(x0 + 14, y + 60, 36, YELLOW, escape(b.detail, int(w / 15))), 1))
    return ev


def strip_ass(rows, error=None):
    """(ASS document, height, cycle): the rows repeated to fill the visible grid plus one full cycle."""
    if error or not rows:
        cycle = GRID_VISIBLE
        ev = [_event(0, 1, 'message', _text(W // 2, y + GRID_VISIBLE // 2, 64, YELLOW, escape(error or 'NO LISTINGS', 60),
                                            an=5)) for y in (0, GRID_VISIBLE)]
        return _doc(2 * cycle, ev), 2 * cycle, cycle
    cycle = len(rows) * ROW_H
    reps = -(-GRID_VISIBLE // cycle) + 1
    ev = []
    for r in range(reps):
        for i, row in enumerate(rows):
            ev += _row(row, i, r * cycle + i * ROW_H)
    return _doc(reps * cycle, ev), reps * cycle, cycle


def overlay_ass(promos, start, seconds):
    ev = [_event(i, i + 1, 'clock', _text(PROMO_W + 320, 170, 120, YELLOW, _clock(start + timedelta(seconds=i)), an=5))
          for i in range(seconds)]
    if promos:
        first = int(start.timestamp()) // PROMO_S
        for slot in range(-(-seconds // PROMO_S)):
            p = promos[(first + slot) % len(promos)]
            t0, t1 = slot * PROMO_S, min(seconds, (slot + 1) * PROMO_S)
            head = ('NOW ON ' if p.kind == 'NOW' else 'COMING UP ON ') + p.programme.channel_name.upper()
            title = escape(p.programme.title, 46)
            if p.programme.detail:
                title += f'{{\\1c{colour(YELLOW)}\\fs40}}  {escape(p.programme.detail, 40)}'
            ev += [
                _event(t0, t1, 'promo', _box(0, 390, PROMO_W, 150, BLACK, '\\1a&H50&')),
                _event(t0, t1, 'promo', _text(40, 400, 60, YELLOW, escape(head, 40)), 1),
                _event(t0, t1, 'promo', _text(40, 466, 52, WHITE, title), 1),
                _event(t0, t1, 'promo', _text(PROMO_W - 40, 400, 60, WHITE, f'CHANNEL {p.programme.channel}', an=9), 1),
            ]
    return _doc(H, ev)
