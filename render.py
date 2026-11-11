"""Render the profile README cards into cards/.

Reads data/github.json (refreshed daily by .github/workflows/cards.yml) and writes
one animated SVG per card. Every card is shown through an <img> tag, so each SVG
is self-contained: an inline <style> with CSS keyframes, SMIL animations, and fonts
subset with pyftsubset and embedded as base64 WOFF data URIs.

    python render.py            animated cards
    STATIC=1 python render.py   final frame only, plus preview.html for a screenshot

portrait.jpg (or .png) next to this file becomes the ASCII portrait in the hero.
"""

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from xml.dom import minidom
from xml.sax.saxutils import escape

from fontTools.ttLib import TTFont

ROOT = Path(__file__).parent
OUT = ROOT / "cards"
STATIC = os.environ.get("STATIC") == "1"

# --- Look: paper, ink and three accents --------------------------------------
PAPER, INK, INK2, MUTED, RULE, EMPTY = "#f3f3f1", "#111111", "#3a3a3c", "#6e6e73", "#dcdcd7", "#e2e2de"
BLUE, RED, YELLOW, WHITE = "#2f55ff", "#ef3b2d", "#ffc81a", "#ffffff"
BLUES = [EMPTY, "#c8d3ff", "#94a8ff", "#6280ff", BLUE, "#1a3be0"]  # heatmap levels 0-5
PARTIAL = "#c9c9c4"  # the month still in progress

FONTS = {  # CSS family -> (file, fallback stack)
    "Instrument Serif": ("fonts/InstrumentSerif-Regular.ttf", "Georgia, serif"),
    "Instrument Serif Italic": ("fonts/InstrumentSerif-Italic.ttf", "Georgia, serif"),
    "Geist": ("fonts/Geist-Regular.ttf", "system-ui, -apple-system, sans-serif"),
    "Geist Mono": ("fonts/GeistMono-Regular.ttf", "ui-monospace, SFMono-Regular, Menlo, monospace"),
}
SERIF, ITALIC, SANS, MONO = FONTS

# --- Content ------------------------------------------------------------------
LOGIN = "Soumyadip2003-AI"
FIRST, LAST = "Soumyadip", "Sarkar"
EYEBROW = "SOFTWARE ENGINEER IN KOLKATA"
BIO = "I build AI systems that don’t fall over, from autonomous agents to calibrated risk models."
SITE = "portfolio-soumyadip.vercel.app"

FEATURED = [  # (repo, display name, one line or None for the GitHub description)
    ("Nova", "Nova", "Autonomous GPT-5.1 agent that turns a plain description into a working Next.js app."),
    ("reposcribe", "RepoScribe", "Ask questions about any repo, get a scored review, and open reviewed fix pull requests."),
    ("stroke-prediction-system-ai-ml", "NeuroPredict", "Calibrated stroke-risk model behind an interactive React front end."),
    ("verus-deepfake-detector", "Verus", "Face-focused deepfake detection with GenD CLIP and an evaluation workflow."),
]

# Crop of the photo, as fractions: left, top, width. Height follows the portrait box.
PORTRAIT_CROP = (.22, .03, .66)

# When each card starts, so the page plays top to bottom.
START = {"hero": 0.0, "heatmap": 1.4, "activity": 2.3, "tiles": 3.0, "repos": 3.6}

# --- Fonts --------------------------------------------------------------------
_metrics = {}


def measure(text, family, size, ls=0):
    """Advance width in px, without kerning, plus letter-spacing."""
    if family not in _metrics:
        f = TTFont(ROOT / FONTS[family][0])
        _metrics[family] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    cmap, hmtx, upem = _metrics[family]
    return sum(hmtx[cmap.get(ord(c), ".notdef")][0] for c in text) * size / upem + ls * max(0, len(text) - 1)


def woff_data_uri(family, chars):
    """Subset one font to exactly these characters with pyftsubset."""
    with tempfile.TemporaryDirectory() as tmp:
        txt, out = Path(tmp, "chars.txt"), Path(tmp, "out.woff")
        txt.write_text("".join(sorted(chars | {" "})), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "fontTools.subset", str(ROOT / FONTS[family][0]),
                        f"--text-file={txt}", f"--output-file={out}", "--flavor=woff",
                        "--layout-features=kern,liga", "--no-hinting", "--desubroutinize",
                        "--drop-tables+=meta"], check=True)
        return "data:font/woff;base64," + base64.b64encode(out.read_bytes()).decode()


def wrap(text, family, size, max_width, max_lines):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and measure(trial, family, size) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while measure(last + "…", family, size) > max_width:
            last = last.rsplit(" ", 1)[0]
        lines = lines[: max_lines - 1] + [last + "…"]
    return lines


# --- SVG building --------------------------------------------------------------
# Elements rest at their final state; keyframes only say where they come from, so
# "animation:none" under reduced motion shows the finished card. SMIL ignores CSS,
# so SMIL-driven pieces live in .smil (hidden then) with a still twin in .still.
MOTION_CSS = """
.up{animation:up .8s cubic-bezier(.2,.7,.2,1) both}
@keyframes up{from{opacity:0;transform:translateY(18px)}}
.fade{animation:fade .35s ease-out both}
@keyframes fade{from{opacity:0}}
.grow{animation:grow .9s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:50% 100%}
@keyframes grow{from{transform:scaleY(0)}}
.pop{animation:pop .45s cubic-bezier(.3,1.6,.5,1) both;transform-box:fill-box;transform-origin:center}
@keyframes pop{from{opacity:0;transform:scale(0)}}
.swing{animation:swing 1.1s cubic-bezier(.2,.8,.2,1) both;transform-box:view-box;transform-origin:0 100%}
@keyframes swing{0%{opacity:0;transform:translateY(90px) rotate(8deg)}60%{opacity:1;transform:translateY(-6px) rotate(-1.5deg)}100%{transform:none}}
.nudge{animation:nudge 1.8s ease-in-out infinite}
@keyframes nudge{50%{transform:translateX(4px)}}
.pulse{animation:pulse 2.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.2}}
.still{display:none}
@media (prefers-reduced-motion:reduce){*{animation:none!important}.smil{display:none}.still{display:inline}}
"""
STATIC_CSS = ".smil{display:none}"


class Card:
    def __init__(self, name, width, height, title, start):
        self.name, self.w, self.h, self.title, self.start = name, width, height, title, start
        self.defs, self.body = [], []
        self.chars = defaultdict(set)

    def at(self, delay):
        """Absolute begin time for SMIL, relative to the card's start."""
        return f"{self.start + delay:.2f}s"

    def anim(self, cls, delay=0):
        """class + delay attributes. Nothing when STATIC, so elements sit at their final state."""
        if STATIC:
            return ""
        return f' class="{cls}" style="animation-delay:{self.start + delay:.3f}s"'

    def t(self, x, y, s, family=SANS, size=14, fill=INK, anchor=None, ls=0, extra=""):
        self.chars[family].update(s)
        attrs = f' text-anchor="{anchor}"' if anchor else ""
        attrs += f' letter-spacing="{ls}"' if ls else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="\'{family}\', {FONTS[family][1]}" '
                f'font-size="{size}" fill="{fill}"{attrs}{extra}>{escape(s)}</text>')

    def save(self, fill=PAPER):
        faces = "".join(
            f"@font-face{{font-family:'{fam}';src:url({woff_data_uri(fam, chars)}) format('woff')}}"
            for fam, chars in sorted(self.chars.items()))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="title">'
               f'<title id="title">{escape(self.title)}</title>'
               f'<style>{faces}{STATIC_CSS if STATIC else MOTION_CSS}</style>'
               f'<defs>{"".join(self.defs)}</defs>'
               + (f'<rect width="{self.w}" height="{self.h}" rx="18" fill="{fill}"/>' if fill else "")
               + f'{"".join(self.body)}</svg>')
        minidom.parseString(svg)  # a malformed card fails the build instead of being committed
        (OUT / f"{self.name}.svg").write_text(svg, encoding="utf-8")


def arrow(x, y, colour, diagonal=False):
    """A small arrow: diagonal for an outbound link, straight for a tile."""
    d = (f"M{x} {y + 10}L{x + 10} {y}M{x + 2} {y}H{x + 10}V{y + 8}" if diagonal
         else f"M{x} {y + 6}H{x + 13}M{x + 8} {y + 1}L{x + 13} {y + 6}L{x + 8} {y + 11}")
    return (f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def eyebrow(c, x, y, s, fill=INK2, size=13, anchor=None):
    return c.t(x, y, s, MONO, size, fill, anchor=anchor, ls=2)


# --- Data ----------------------------------------------------------------------
def days_from(data):
    weeks = data["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [(dt.date.fromisoformat(d["date"]), d["contributionCount"]) for w in weeks for d in w["contributionDays"]]


def streaks(days):
    """(current, longest). Today with no contributions yet does not break the streak."""
    longest = run = 0
    for _, n in days:
        run = run + 1 if n else 0
        longest = max(longest, run)
    counts = [n for _, n in days]
    if counts and counts[-1] == 0:
        counts.pop()
    current = 0
    for n in reversed(counts):
        if not n:
            break
        current += 1
    return current, longest


# --- Cards ----------------------------------------------------------------------
def ascii_rows(path, cols, rows, aspect):
    """The photo as rows of ASCII for ink on paper: dark pixels become dense glyphs,
    the bright background drops out to spaces."""
    from PIL import Image, ImageFilter, ImageOps

    img = ImageOps.exif_transpose(Image.open(path)).convert("L")
    w, h = img.size
    left, top, width = PORTRAIT_CROP
    box = (int(left * w), int(top * h), int((left + width) * w), int(top * h + width * w / aspect))
    img = img.crop(box).filter(ImageFilter.UnsharpMask(radius=2, percent=140))
    img = ImageOps.autocontrast(img, cutoff=1).resize((cols, rows), Image.LANCZOS)
    ramp, floor = " .:-=+*#%@", .3
    out = []
    for r in range(rows):
        line = ""
        for c in range(cols):
            # Fade out towards an oval edge, so the room behind drops away instead of framing a box.
            edge = ((c - cols * .55) / (cols * .5)) ** 2 + ((r - rows * .5) / (rows * .52)) ** 2
            dark = (1 - img.getpixel((c, r)) / 255) * min(1, max(0, (1 - edge) / .3))
            line += " " if dark < floor else ramp[min(len(ramp) - 1, 1 + int((dark - floor) / (1 - floor) * (len(ramp) - 1)))]
        out.append(line.rstrip())
    return out


def hero():
    W, H = 880, 432
    c = Card("hero", W, H, f"{FIRST} {LAST}. {EYEBROW.capitalize()}. {BIO}", START["hero"])
    b = c.body

    # Eyebrow with a pulsing square.
    b.append(f'<g{c.anim("up", .05)}><rect x="56" y="58" width="10" height="10" rx="2" fill="{BLUE}"'
             f'{c.anim("pulse", 1.2)}/>{eyebrow(c, 76, 68, EYEBROW)}</g>')

    # The name, one line at a time; a blue shine sweeps across the surname.
    size = 104
    while max(measure(FIRST, SERIF, size), measure(LAST, ITALIC, size) + 12) > 450:
        size -= 2
    b.append(f'<g{c.anim("up", .15)}>{c.t(52, 196, FIRST, SERIF, size, INK, ls=-1.5)}</g>')
    c.defs.append(
        f'<linearGradient id="shine" gradientUnits="userSpaceOnUse" x1="-300" y1="0" x2="0" y2="0">'
        f'<stop offset="0" stop-color="{INK}"/><stop offset=".4" stop-color="{INK}"/>'
        f'<stop offset=".5" stop-color="{BLUE}"/><stop offset=".6" stop-color="{INK}"/><stop offset="1" stop-color="{INK}"/>'
        f'<animateTransform attributeName="gradientTransform" type="translate" values="0 0;{W} 0;{W} 0" '
        f'keyTimes="0;.35;1" dur="5s" begin="{c.at(1)}" repeatCount="indefinite"/></linearGradient>')

    def surname(fill):
        return c.t(64, 292, LAST, ITALIC, size, fill, ls=-1.5)

    b.append(f'<g{c.anim("up", .3)}><g class="smil">{surname("url(#shine)")}</g><g class="still">{surname(INK)}</g></g>')

    for i, line in enumerate(wrap(BIO, SANS, 19, 450, 2)):
        b.append(f'<g{c.anim("up", .45 + i * .06)}>{c.t(56, 338 + i * 26, line, SANS, 19, INK2)}</g>')
    b.append(f'<g{c.anim("up", .6)}>{c.t(56, 400, SITE, MONO, 14, INK)}'
             f'{arrow(56 + measure(SITE, MONO, 14) + 14, 389, BLUE, diagonal=True)}</g>')

    # ASCII portrait, printed one row at a time like a slow terminal.
    portrait = next((p for p in (ROOT / "portrait.jpg", ROOT / "portrait.png") if p.exists()), None)
    if portrait:
        x0, y0, bw, bh, rows = 520, 24, 330, 392, 96
        pitch = bh / rows
        fsize = round(pitch * 1.12, 2)
        cw = measure("M", MONO, fsize)
        cols = int(bw / cw)
        for r, line in enumerate(ascii_rows(portrait, cols, rows, (cols * cw) / bh)):
            if line.strip():
                c.chars[MONO].update(line)
                b.append(f'<g{c.anim("fade", .5 + r * .018)}><text x="{x0}" y="{y0 + (r + 1) * pitch:.1f}" '
                         f'xml:space="preserve" style="white-space:pre" font-family="\'{MONO}\', {FONTS[MONO][1]}" '
                         f'font-size="{fsize}" fill="{INK}">{escape(line)}</text></g>')
    c.save()


def heatmap(data):
    W, H = 880, 262
    days = days_from(data)
    total = data["contributionsCollection"]["contributionCalendar"]["totalContributions"]
    nonzero = sorted(n for _, n in days if n)
    cuts = [nonzero[int(len(nonzero) * q)] for q in (.2, .4, .6, .8)] if nonzero else [0] * 4

    def level(n):
        return 0 if not n else 1 + sum(n > t for t in cuts)

    c = Card("heatmap", W, H, f"{total:,} contributions in the last year, one square per day.", START["heatmap"])
    b = c.body
    lead = "The year, "
    b.append(f'<g{c.anim("up", 0)}>{c.t(54, 84, lead, SERIF, 48, INK, ls=-.5)}'
             f'{c.t(54 + measure(lead, SERIF, 48, -.5), 84, "in blue", ITALIC, 48, BLUE, ls=-.5)}</g>')
    b.append(f'<g{c.anim("up", .08)}>{eyebrow(c, W - 56, 82, "REFRESHED DAILY", MUTED, 12, anchor="end")}</g>')

    first_sunday = days[0][0] - dt.timedelta(days=(days[0][0].weekday() + 1) % 7)
    cols = (days[-1][0] - first_sunday).days // 7 + 1
    pitch = (W - 112) / cols
    cell, x0, y0 = pitch - 3, 56, 110
    ly = y0 + 7 * pitch + 11
    lx = W - 56 - measure("more", MONO, 12) - 8 - 6 * 14
    legend_left = lx - 8 - measure("less", MONO, 12) - 16
    last_label = -9
    for d, n in days:
        col, row = (d - first_sunday).days // 7, (d.weekday() + 1) % 7
        x = x0 + col * pitch
        b.append(f'<rect x="{x:.1f}" y="{y0 + row * pitch:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="3" '
                 f'fill="{BLUES[level(n)]}"{c.anim("pop", .2 + col * .02 + row * .006)}/>')
        if d.day == 1 and col - last_label >= 4 and x + measure(f"{d:%b}", MONO, 12) < legend_left:
            b.append(c.t(x, y0 + 7 * pitch + 22, f"{d:%b}", MONO, 12, MUTED))
            last_label = col

    b.append(c.t(lx - 8, ly + 11, "less", MONO, 12, MUTED, anchor="end"))
    b += [f'<rect x="{lx + i * 14:.1f}" y="{ly:.1f}" width="11" height="11" rx="2.5" fill="{col}"/>'
          for i, col in enumerate(BLUES)]
    b.append(c.t(lx + 6 * 14 + 5, ly + 11, "more", MONO, 12, MUTED))
    c.save()


def activity(data):
    W, H = 880, 348
    days = days_from(data)
    total = data["contributionsCollection"]["contributionCalendar"]["totalContributions"]
    months = defaultdict(int)
    for d, n in days:
        months[(d.year, d.month)] += n
    months = sorted(months.items())[-12:]
    current, longest = streaks(days)
    active = sum(1 for _, n in days if n)
    busy_day, busy_n = max(days, key=lambda x: x[1])
    best = max(range(len(months)), key=lambda i: months[i][1])
    in_progress = len(months) - 1  # the current month is partial

    c = Card("activity", W, H, f"{total:,} contributions in the last 12 months. Current streak {current} days, "
             f"longest {longest}. {active} of {len(days)} days with a commit. {busy_n} on the busiest day, "
             f"{busy_day.day} {busy_day:%b %Y}.", START["activity"])
    b = c.body
    b.append(f'<g{c.anim("up", 0)}>{eyebrow(c, 48, 62, "THE LAST 12 MONTHS ON GITHUB")}</g>')
    num = f"{total:,} "
    b.append(f'<g{c.anim("up", .08)}>{c.t(44, 130, num, SERIF, 70, INK, ls=-1.5)}'
             f'{c.t(44 + measure(num, SERIF, 70, -1.5), 130, "contributions", ITALIC, 70, INK, ls=-1.5)}</g>')

    base, top, slot, bw = 296, 162, 47.5, 38
    peak = max(n for _, n in months) or 1
    for i, ((y, m), n) in enumerate(months):
        x = 46 + i * slot
        fill = BLUE if i == best else PARTIAL if i == in_progress else INK
        h = max(3, (base - top) * n / peak)
        b.append(f'<rect x="{x}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="5" '
                 f'fill="{fill if n else RULE}"{c.anim("grow", .2 + i * .05)}/>')
        b.append(c.t(x + bw / 2, base + 26, f"{dt.date(y, m, 1):%b}", MONO, 13,
                     MUTED if i == in_progress else INK2, anchor="middle"))
        if i == best:
            b.append(f'<g{c.anim("pop", .9 + i * .05)}>'
                     f'{c.t(x + bw / 2, base - h - 10, f"{n:,}", MONO, 11, BLUE, anchor="middle")}</g>')

    sx = 664
    streak_sub = "current streak, and the longest" if current == longest else f"current streak, longest {longest}"
    stats = [(f"{current} ", "days" if current != 1 else "day", streak_sub),
             (f"{active} ", f"/ {len(days)}", "days with a commit"),
             (f"{busy_n}", "", f"on the busiest day, {busy_day.day} {busy_day:%b}")]
    for i, (big, tail, sub) in enumerate(stats):
        y = 96 + i * 96
        tail_font, tail_fill = (ITALIC, INK) if i == 0 else (SERIF, MUTED)
        tail_svg = c.t(sx + measure(big, SERIF, 50, -1), y, tail, tail_font, 50, tail_fill, ls=-1) if tail else ""
        b.append(f'<g{c.anim("up", .6 + i * .12)}>{c.t(sx, y, big, SERIF, 50, INK, ls=-1)}{tail_svg}'
                 f'{c.t(sx + 1, y + 28, sub, SANS, 14, MUTED)}</g>')
        if i < 2:
            b.append(f'<line x1="{sx}" y1="{y + 50.5}" x2="{W - 40}" y2="{y + 50.5}" stroke="{RULE}"'
                     f'{c.anim("fade", .7 + i * .12)}/>')
    c.save()


def count_up(c, x, y, number, size, fill, begin, step=.05):
    """The number counts up from zero in up to 18 SMIL frames. A still twin shows the final value."""
    frames = sorted({round(number * (1 - (1 - k / 17) ** 3)) for k in range(18)})
    shown = []
    for k, v in enumerate(frames):
        timing = (f'begin="{c.at(begin + k * step)}" fill="freeze"' if k == len(frames) - 1
                  else f'begin="{c.at(begin + k * step)}" end="{c.at(begin + (k + 1) * step)}"')
        shown.append(c.t(x, y, str(v), SERIF, size, fill, ls=-1, extra=' opacity="0"')
                     .replace("</text>", f'<set attributeName="opacity" to="1" {timing}/></text>'))
    return f'<g class="smil">{"".join(shown)}</g><g class="still">{c.t(x, y, str(number), SERIF, size, fill, ls=-1)}</g>'


def tiles(data):
    repos = [r for r in data["repositories"]["nodes"] if r["name"] != LOGIN]
    live = [r for r in repos if r.get("homepageUrl")]
    specs = [  # (file, label, colour, text colour, number, word, body)
        ("tile-build", "BUILD", BLUE, WHITE, len(repos), "public repos",
         "Nova, RepoScribe, NeuroPredict and more. Full-stack and AI, start to finish."),
        ("tile-ship", "SHIP", RED, WHITE, len(live), "live apps",
         "Every one deployed and running. Try them from my portfolio."),
        ("tile-connect", "CONNECT", YELLOW, INK, 1, "message away",
         "LinkedIn is quickest. Or email soumyadip.0202@gmail.com."),
    ]
    for i, (name, label, bg, fg, number, word, body) in enumerate(specs):
        W, H = 286, 210
        c = Card(name, W, H, f"{label.capitalize()}: {number} {word}. {body}", START["tiles"] + i * .15)
        c.defs.append(f'<clipPath id="tile"><rect width="{W}" height="{H}" rx="16"/></clipPath>')
        size, nx, ny = 54, 22, 112
        inner = [f'<rect width="{W}" height="{H}" rx="16" fill="{bg}"/>', eyebrow(c, 24, 38, label, fg, 13),
                 f'<g{c.anim("nudge", 1.4)}>{arrow(W - 40, 27, fg)}</g>',
                 count_up(c, nx, ny, number, size, fg, begin=.6),
                 c.t(nx + measure(f"{number} ", SERIF, size, -1), ny, word, ITALIC, size * .82, fg, ls=-.5)]
        for j, line in enumerate(wrap(body, SANS, 14.5, W - 48, 3)):
            inner.append(c.t(24, 150 + j * 21, line, SANS, 14.5, fg))

        # A ring pulses from the corner and a shine sweeps across, both on a loop.
        inner.append(
            f'<g class="smil"><circle cx="{W - 30}" cy="{H - 30}" r="0" fill="none" stroke="{fg}" stroke-opacity="0">'
            f'<animate attributeName="r" values="0;60" dur="2.8s" begin="{c.at(1.4)}" repeatCount="indefinite"/>'
            f'<animate attributeName="stroke-opacity" values=".55;0" dur="2.8s" begin="{c.at(1.4)}" repeatCount="indefinite"/></circle>'
            f'<rect x="-140" y="-40" width="70" height="{H + 80}" fill="{WHITE}" opacity=".18" transform="skewX(-18)">'
            f'<animate attributeName="x" values="-140;{W + 120};{W + 120}" keyTimes="0;.25;1" dur="6s" '
            f'begin="{c.at(1.8 + i * .4)}" repeatCount="indefinite"/></rect></g>')
        c.body.append(f'<g{c.anim("swing", 0)}><g clip-path="url(#tile)">{"".join(inner)}</g></g>')
        c.save(fill=None)


def repo_cards(data):
    repos = {r["name"]: r for r in data["repositories"]["nodes"]}
    accents = [BLUE, RED, YELLOW, BLUE]
    for i, (key, display, line) in enumerate(FEATURED):
        r = repos[key]
        about = line or r["description"] or ""
        W, H = 434, 236
        c = Card(f"repo-{key.lower()}", W, H, f"{display}: {about}", START["repos"] + (i // 2) * .25 + (i % 2) * .1)
        b = c.body
        lang = (r.get("primaryLanguage") or {}).get("name", "Code").upper()
        updated = f"UPDATED {dt.date.fromisoformat(r['pushedAt'][:10]):%b %Y}".upper()
        b.append(f'<g{c.anim("up", 0)}><rect x="26" y="34" width="10" height="10" rx="2" fill="{accents[i]}"/>'
                 f'{eyebrow(c, 46, 44, lang, INK2, 12)}{eyebrow(c, W - 26, 44, updated, MUTED, 12, anchor="end")}</g>')
        b.append(f'<g{c.anim("up", .08)}>{c.t(24, 104, display, SERIF, 50, INK, ls=-1)}</g>')
        for j, ln in enumerate(wrap(about, SANS, 15.5, W - 52, 3)):
            b.append(f'<g{c.anim("up", .14 + j * .04)}>{c.t(26, 138 + j * 22, ln, SANS, 15.5, INK2)}</g>')
        link = r["homepageUrl"].split("//")[-1].rstrip("/") if r.get("homepageUrl") else f"github.com/{LOGIN}/{key}"
        b.append(f'<g{c.anim("up", .3)}>{c.t(26, 212, link, MONO, 12.5, INK)}'
                 f'<g{c.anim("nudge", 1.2)}>{arrow(26 + measure(link, MONO, 12.5) + 12, 202, BLUE, diagonal=True)}</g></g>')
        c.save()


def preview():
    """README on a plain page, so the STATIC=1 output can be screenshotted."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    (ROOT / "preview.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Profile preview</title>'
        '<body style="margin:0;background:#fff;font-family:system-ui">'
        f'<main style="max-width:880px;margin:32px auto;padding:0 16px">{readme}</main>', encoding="utf-8")


def demo():
    """Self-check for the streak and wrap logic."""
    d = dt.date(2026, 1, 1)

    def seq(ns):
        return [(d + dt.timedelta(i), n) for i, n in enumerate(ns)]

    assert streaks(seq([1, 1, 0, 1, 1, 1])) == (3, 3)
    assert streaks(seq([1, 1, 0, 1, 1, 0])) == (2, 2)  # today empty: streak still alive
    assert streaks(seq([1, 0, 0])) == (0, 1)
    assert len(wrap("word " * 60, SANS, 15, 300, 2)) == 2


if __name__ == "__main__":
    demo()
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    data = json.loads((ROOT / "data/github.json").read_text(encoding="utf-8"))["data"]["user"]
    hero(), heatmap(data), activity(data), tiles(data), repo_cards(data)
    if STATIC:
        preview()
    print(f"Wrote {len(list(OUT.glob('*.svg')))} cards to {OUT}{' (static)' if STATIC else ''}")
