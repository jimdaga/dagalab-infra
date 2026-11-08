"""The Prevue screen as one ASS subtitle document: panels, promo captions, clock and the scrolling grid.

ffmpeg's `ass` filter (libass) draws it over the promo clips. Everything time-dependent that must line up across the
60-second items ErsatzTV chains together (grid scroll, promo rotation) is derived from wall-clock time.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/New_York')
W, H = 1920, 1080
PROMO_W, TOP_H = 1280, 540                 # promo box 0,0 1280x540; side panel 1280,0 640x540
GRID_TOP = 594                             # time bar 540..594, rows scroll below it
ROW_H, TAB_W, COL_W = 108, 420, 500        # 3 half-hour columns of 500 px
SCROLL_PX_PER_S = 40
PROMO_S = 10
FONT = 'Barlow Condensed'

BG, SIDE, BLOCK = '#06106B', '#0A1A6B', '#2E48C9'
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


def grid_positions(n_rows, start, t, below=0):
    """(row index, top y) of every row instance on screen at `t` seconds into the item that starts at `start`;
    `below` extends the range past the bottom edge (rows that scroll in later)."""
    cycle = n_rows * ROW_H
    offset = (start.timestamp() + t) * SCROLL_PX_PER_S % cycle
    out, k = [], 0
    while True:
        y = GRID_TOP + k * ROW_H - offset
        if y >= H + below:
            return out
        if y + ROW_H > GRID_TOP - ROW_H:
            out.append((k % n_rows, round(y, 3)))
        k += 1


def _header():
    return '\n'.join([
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {W}', f'PlayResY: {H}', 'WrapStyle: 2',
        'ScaledBorderAndShadow: yes', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
        'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, '
        'MarginR, MarginV, Encoding',
        f'Style: G,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,2,7,0,0,0,1', '',
        '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'])


def _static(seconds, win):
    end = seconds
    ev = [
        _event(0, end, 'static', _box(PROMO_W, 0, W - PROMO_W, TOP_H, SIDE)),
        _event(0, end, 'static', _box(PROMO_W, 0, 6, TOP_H, GOLD), 1),
        _event(0, end, 'static', _text(PROMO_W + 320, 330, 64, WHITE, 'DAGAFLIX', an=5), 1),
        _event(0, end, 'static', _text(PROMO_W + 320, 400, 64, YELLOW, 'TV GUIDE', an=5), 1),
        _event(0, end, 'static', _box(0, TOP_H, W, H - TOP_H, BG)),
        # time bar above the rows (higher layer, so rows scroll underneath it)
        _event(0, end, 'static', _box(0, TOP_H, W, GRID_TOP - TOP_H, GOLD), 5),
        _event(0, end, 'static', _box(0, TOP_H, W, 4, BLACK), 6),
        _event(0, end, 'static', _text(20, TOP_H + 27, 40, BLACK,
                                       win[0].astimezone(TZ).strftime('%a %b %-d').upper(), an=4, extra='\\shad0'), 6),
    ]
    for i in range(3):
        x = TAB_W + i * COL_W
        ev.append(_event(0, end, 'static', _box(x, TOP_H, 3, GRID_TOP - TOP_H, BLACK), 6))
        ev.append(_event(0, end, 'static', _text(x + 16, TOP_H + 27, 40, BLACK,
                                                 _clock(win[0] + timedelta(minutes=30 * i)), an=4, extra='\\shad0'), 6))
    return ev


def _rows(rows, start, seconds):
    clip = f'\\clip(0,{GRID_TOP},{W},{H})'
    travel = SCROLL_PX_PER_S * seconds
    ev = []
    for idx, y0 in grid_positions(len(rows), start, 0, below=travel):
        for x, dy, body in _row_pieces(rows[idx], idx):
            y = y0 + dy
            move = f'\\move({x},{y:.1f},{x},{y - travel:.1f})'
            ev.append(_event(0, seconds, 'row', body.replace('\\pos(POS)', move + clip), 2 if '\\p1' in body else 3))
    return ev


def _row_pieces(row, idx):
    """(x, dy, ASS body with a \\pos(POS) placeholder) for each piece of one grid row."""
    tab = RED if idx % 2 == 0 else GREEN
    pieces = [
        (0, 0, f'{{\\an7\\pos(POS)\\bord0\\shad0\\1c{colour(BLACK)}\\p1}}m 0 0 l {W} 0 {W} {ROW_H} 0 {ROW_H}{{\\p0}}'),
        (0, 2, f'{{\\an7\\pos(POS)\\bord0\\shad0\\1c{colour(tab)}\\p1}}m 0 0 l {TAB_W - 3} 0 {TAB_W - 3} {ROW_H - 4} 0 {ROW_H - 4}{{\\p0}}'),
        (20, 8, f'{{\\an7\\pos(POS)\\fs56\\b1\\1c{colour(WHITE)}\\shad3\\bord0}}{row.channel}'),
        (20, 58, f'{{\\an7\\pos(POS)\\fs40\\b1\\1c{colour(YELLOW)}\\shad3\\bord0}}{escape(row.name.upper(), 18)}'),
    ]
    for b in row.blocks:
        x0 = TAB_W + round(b.x0 * 3 * COL_W)
        w = TAB_W + round(b.x1 * 3 * COL_W) - x0 - 3
        if w < 20:
            continue
        chars = max(3, int(w / 19))
        pieces.append((x0, 2, f'{{\\an7\\pos(POS)\\bord0\\shad0\\1c{colour(BLOCK)}\\p1}}m 0 0 l {w} 0 {w} {ROW_H - 4} 0 {ROW_H - 4}{{\\p0}}'))
        pieces.append((x0 + 14, 10, f'{{\\an7\\pos(POS)\\fs46\\b1\\1c{colour(WHITE)}\\shad3\\bord0}}{escape(b.title, chars)}'))
        if b.detail:
            pieces.append((x0 + 14, 60, f'{{\\an7\\pos(POS)\\fs36\\b1\\1c{colour(YELLOW)}\\shad3\\bord0}}{escape(b.detail, int(w / 15))}'))
    return pieces


def _promos(promos, start, seconds):
    ev = []
    if not promos:
        return ev
    first = int(start.timestamp()) // PROMO_S
    for slot in range(seconds // PROMO_S + (1 if seconds % PROMO_S else 0)):
        p = promos[(first + slot) % len(promos)]
        t0, t1 = slot * PROMO_S, min(seconds, (slot + 1) * PROMO_S)
        head = ('NOW ON ' if p.kind == 'NOW' else 'COMING UP ON ') + p.programme.channel_name.upper()
        ev.append(_event(t0, t1, 'promo', _box(0, 390, PROMO_W, 150, BLACK, '\\1a&H50&'), 1))
        ev.append(_event(t0, t1, 'promo', _text(40, 400, 60, YELLOW, escape(head, 40)), 2))
        title = escape(p.programme.title, 46)
        if p.programme.detail:
            title += f'{{\\1c{colour(YELLOW)}\\fs40}}  {escape(p.programme.detail, 40)}'
        ev.append(_event(t0, t1, 'promo', _text(40, 466, 52, WHITE, title), 2))
        ev.append(_event(t0, t1, 'promo', _text(PROMO_W - 40, 400, 60, WHITE, f'CHANNEL {p.programme.channel}', an=9), 2))
    return ev


def build_ass(rows, promos, win, start, seconds, error=None):
    ev = _static(seconds, win)
    ev += [_event(i, i + 1, 'clock', _text(PROMO_W + 320, 170, 120, YELLOW, _clock(start + timedelta(seconds=i)), an=5), 2)
           for i in range(seconds)]
    if error or not rows:
        ev.append(_event(0, seconds, 'static', _text(W // 2, (GRID_TOP + H) // 2, 64, YELLOW,
                                                     escape(error or 'NO LISTINGS', 60), an=5), 3))
    else:
        ev += _rows(rows, start, seconds)
    ev += _promos(promos, start, seconds)
    return _header() + '\n' + '\n'.join(ev) + '\n'
