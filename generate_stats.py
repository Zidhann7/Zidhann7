#!/usr/bin/env python3
"""
Generate an animated GitHub statistics card (stats.svg) for a GitHub user.

Data layer   : fetch_calendar_graphql / fetch_calendar_html / fetch_repo_totals
Stats layer  : compute_stats
Render layer : render_svg

Nothing is hardcoded or estimated. If a metric cannot be obtained, it is
rendered as "N/A" (unavailable) instead of a made-up value.

Environment variables
  GITHUB_USER  GitHub username (default: Zidhann7)
  GH_TOKEN     Optional token. With it: GraphQL day-level calendar + repo/star totals.
               Without it: public contribution-calendar HTML (still day-level) and
               unauthenticated REST for repo/star totals (may be rate limited).
  STATS_OUT    Output path (default: stats.svg next to this script)
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import OrderedDict

USER = os.environ.get("GITHUB_USER", "Zidhann7")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT = os.environ.get(
    "STATS_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.svg")
)
HEADER_TITLE = "zidan@github: ~$ ./stats.sh"
SKILLS = ["Excel", "MySQL / SQL", "Python", "Power BI", "Tableau", "Gen AI"]

# ───────────────────────────── data layer ──────────────────────────────

def http(url, data=None, headers=None):
    h = {"User-Agent": "stats-card-generator"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


GQL = """
query($login:String!, $after:String) {
  user(login:$login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, first: 100, after: $after) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { stargazerCount }
    }
  }
}
"""


def fetch_graphql(user, token):
    """Day-level calendar + public repo count + star total via GraphQL."""
    days, repos, stars, after = [], None, 0, None
    while True:
        body = json.dumps({"query": GQL, "variables": {"login": user, "after": after}}).encode()
        raw = http(
            "https://api.github.com/graphql",
            data=body,
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        )
        payload = json.loads(raw)
        if payload.get("errors") or not payload.get("data", {}).get("user"):
            raise RuntimeError(f"GraphQL error: {payload.get('errors')}")
        u = payload["data"]["user"]
        if after is None:
            for w in u["contributionsCollection"]["contributionCalendar"]["weeks"]:
                for d in w["contributionDays"]:
                    days.append((dt.date.fromisoformat(d["date"]), int(d["contributionCount"])))
            repos = u["repositories"]["totalCount"]
        stars += sum(n["stargazerCount"] for n in u["repositories"]["nodes"])
        pi = u["repositories"]["pageInfo"]
        if not pi["hasNextPage"]:
            break
        after = pi["endCursor"]
    return sorted(set(days)), repos, stars


def fetch_calendar_html(user):
    """Public fallback: parse the day cells + tooltips of the contribution graph."""
    page = http(f"https://github.com/users/{user}/contributions")
    cells = re.findall(r'<td[^>]*data-date="(\d{4}-\d{2}-\d{2})"[^>]*id="([^"]+)"', page)
    tips = {}
    for m in re.finditer(r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>\s*([^<]*)', page):
        tips[m.group(1)] = m.group(2).strip()
    days = []
    for date, cid in cells:
        t = tips.get(cid)
        if t is None:
            raise RuntimeError("tooltip missing for a calendar cell; HTML layout changed")
        m = re.match(r"(No|\d[\d,]*) contributions?", t)
        if not m:
            raise RuntimeError(f"unparseable tooltip: {t!r}")
        n = 0 if m.group(1) == "No" else int(m.group(1).replace(",", ""))
        days.append((dt.date.fromisoformat(date), n))
    if not days:
        raise RuntimeError("no calendar days found")
    return sorted(set(days))


def fetch_repo_totals_rest(user, token=None):
    """Public repo count and star total via REST. Returns (None, None) if unavailable."""
    hdr = {"Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    try:
        prof = json.loads(http(f"https://api.github.com/users/{user}", headers=hdr))
        repos = int(prof["public_repos"])
        stars, page = 0, 1
        while True:
            lst = json.loads(
                http(f"https://api.github.com/users/{user}/repos?per_page=100&page={page}", headers=hdr)
            )
            stars += sum(int(r.get("stargazers_count", 0)) for r in lst)
            if len(lst) < 100:
                break
            page += 1
        return repos, stars
    except Exception as e:  # rate limit, network, etc.
        print(f"[warn] repo/star totals unavailable: {e}", file=sys.stderr)
        return None, None


def collect():
    if TOKEN:
        try:
            days, repos, stars = fetch_graphql(USER, TOKEN)
            return days, repos, stars, "GitHub GraphQL"
        except Exception as e:
            print(f"[warn] GraphQL failed ({e}); falling back to public data", file=sys.stderr)
    days = fetch_calendar_html(USER)
    repos, stars = fetch_repo_totals_rest(USER, TOKEN)
    return days, repos, stars, "GitHub public calendar"

# ───────────────────────────── stats layer ─────────────────────────────

def fmt_d(d):
    return f"{d.strftime('%b')} {d.day}"


def compute_stats(days):
    n = len(days)
    counts = [c for _, c in days]
    total = sum(counts)
    active = sum(1 for c in counts if c > 0)

    # longest streak
    best_len, best_rng, run_start = 0, None, None
    for i, c in enumerate(counts):
        if c > 0:
            if run_start is None:
                run_start = i
            if i - run_start + 1 > best_len:
                best_len, best_rng = i - run_start + 1, (days[run_start][0], days[i][0])
        else:
            run_start = None

    # current streak: today may still be empty without breaking the streak
    i = n - 1
    if counts[i] == 0:
        i -= 1
    end = i
    while i >= 0 and counts[i] > 0:
        i -= 1
    cur_len = end - i
    cur_rng = (days[i + 1][0], days[end][0]) if cur_len > 0 else None

    best_i = max(range(n), key=lambda k: (counts[k], -k))
    months = OrderedDict()
    for d, c in days:
        months[(d.year, d.month)] = months.get((d.year, d.month), 0) + c
    peak_month = max(months.items(), key=lambda kv: kv[1])

    return dict(
        total=total, active=active, span=n,
        cur_len=cur_len, cur_rng=cur_rng, long_len=best_len, long_rng=best_rng,
        best_count=counts[best_i], best_date=days[best_i][0],
        avg=(total / active) if active else 0.0,
        months=months, peak_month=peak_month,
        start=days[0][0], end=days[-1][0],
    )

# ──────────────────────────── render layer ─────────────────────────────

BG1, BG2, BORDER = "#111722", "#0d1117", "#30363d"
CARD, TXT, MUTED = "#161b22", "#e6edf3", "#7d8590"
GREEN, GREEN_D = "#39d353", "#26a641"
STEP = 0.07  # seconds per counter frame
esc = html.escape


def rng_text(r):
    return f"{fmt_d(r[0])} – {fmt_d(r[1])}" if r else "no active streak"


def frames_for(value, decimals, steps=14):
    out, prev = [], None
    for i in range(1, steps):
        v = value * (1 - (1 - i / steps) ** 3)
        v = round(v, decimals) if decimals else int(round(v))
        if v != prev and v != value:
            out.append(v)
            prev = v
    return out


def num(x, y, value, decimals, unit, color, start, size=48):
    """Animated counter: SMIL frame sequence + a CSS-revealed final value.
    Reduced motion hides the frames and shows the final value at once."""
    if value is None:
        return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="700" fill="{MUTED}">N/A</text>')
    fmt = lambda v: f"{v:,.{decimals}f}"
    sub = f'<tspan font-size="{max(size // 2, 14)}" font-weight="400" fill="{MUTED}">{esc(unit)}</tspan>' if unit else ""
    fr = frames_for(value, decimals)
    parts = []
    for k, v in enumerate(fr):
        b, e = start + k * STEP, start + (k + 1) * STEP
        parts.append(
            f'<text class="frame" x="{x}" y="{y}" opacity="0" font-size="{size}" font-weight="700" fill="{color}">'
            f'{fmt(v)}{sub}<set attributeName="opacity" to="1" begin="{b:.3f}s"/>'
            f'<set attributeName="opacity" to="0" begin="{e:.3f}s"/></text>'
        )
    fd = start + len(fr) * STEP
    parts.append(
        f'<text class="final" style="animation-delay:{fd:.3f}s" x="{x}" y="{y}" font-size="{size}" '
        f'font-weight="700" fill="{color}">{fmt(value)}{sub}</text>'
    )
    return "".join(parts)


def card(x, y, w, h, label, value, decimals, unit, color, sub, tip, delay):
    return (
        f'<g class="t card" style="animation-delay:{delay:.2f}s"><title>{esc(tip)}</title>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{CARD}" stroke="{BORDER}"/>'
        f'<text x="{x + 24}" y="{y + 32}" fill="{MUTED}" font-size="15" letter-spacing="1.5">{esc(label)}</text>'
        f'{num(x + 24, y + 80, value, decimals, unit, color, delay + 0.3)}'
        f'<text x="{x + 24}" y="{y + 103}" fill="{MUTED}" font-size="14">{esc(sub)}</text></g>'
    )


def render_svg(s, repos, stars, source):
    W, H = 840, 880
    months = list(s["months"].items())
    peak_key, peak_val = s["peak_month"]
    peak_name = dt.date(peak_key[0], peak_key[1], 1).strftime("%B %Y")
    span_txt = f"{s['start'].strftime('%b')} {s['start'].day}, {s['start'].year} – {fmt_d(s['end'])}, {s['end'].year}"
    pct = round(100 * s["active"] / s["span"]) if s["span"] else 0

    o = []
    o.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'style="max-width:100%;height:auto" role="img" aria-labelledby="ttl dsc" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">'
    )
    o.append(f'<title id="ttl">GitHub analytics dashboard for {esc(USER)}</title>')
    o.append(
        f'<desc id="dsc">Data analyst GitHub activity card for {esc(USER)} covering {esc(span_txt)}. '
        f'{s["total"]:,} contributions on {s["active"]} active days; current streak {s["cur_len"]} days; '
        f'longest streak {s["long_len"]} days; best day {fmt_d(s["best_date"])} with {s["best_count"]} contributions; '
        f'average {s["avg"]:.1f} per active day; highest month {esc(peak_name)} with {peak_val:,}. '
        f'Source: {esc(source)}.</desc>'
    )
    o.append(
        "<style>"
        ".t{opacity:0;animation:in .45s ease-out both}"
        "@keyframes in{0%{opacity:0;transform:translateY(14px)}100%{opacity:1;transform:translateY(0)}}"
        ".final{opacity:0;animation:show .01s linear both}"
        "@keyframes show{to{opacity:1}}"
        ".b{transform-box:fill-box;transform-origin:bottom;transform:scaleY(0);animation:grow .6s ease-out both}"
        "@keyframes grow{to{transform:scaleY(1)}}"
        ".card rect,.bar{transition:stroke .2s,filter .2s}"
        f".card:hover rect{{stroke:{GREEN_D}}}"
        f".bar:hover{{filter:brightness(1.25)}}"
        "@media (prefers-reduced-motion: reduce){"
        ".t,.b{opacity:1!important;transform:none!important;animation:none!important}"
        ".final{opacity:1!important;animation:none!important}"
        ".frame{display:none!important}}"
        "</style>"
    )
    o.append(
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG1}"/>'
        f'<stop offset="1" stop-color="{BG2}"/></linearGradient>'
        '<filter id="glow" x="-50%" y="-20%" width="200%" height="140%">'
        '<feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/>'
        '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
    )
    o.append(f'<rect width="{W}" height="{H}" rx="12" fill="url(#bg)"/>')
    o.append(f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="none" stroke="{BORDER}"/>')
    o.append(f'<line x1="0" y1="30" x2="{W}" y2="30" stroke="{BORDER}"/>')
    for cx, col in ((20, "#ff5f56"), (36, "#ffbd2e"), (52, "#27c93f")):
        o.append(f'<circle cx="{cx}" cy="15" r="5" fill="{col}"/>')
    o.append(f'<text x="420" y="19" fill="{MUTED}" font-size="12" text-anchor="middle">{esc(HEADER_TITLE)}</text>')
    o.append(f'<text x="{W - 16}" y="19" fill="{MUTED}" font-size="11" text-anchor="end" opacity=".7">data as of {s["end"].isoformat()}</text>')

    # six stat cards: 2 cols x 3 rows
    cw, ch, x1, x2 = 392, 114, 20, 428
    ys = (50, 176, 302)
    cur_c = GREEN if s["cur_len"] > 0 else TXT
    o.append(card(x1, ys[0], cw, ch, "CURRENT STREAK", s["cur_len"], 0, " day" if s["cur_len"] == 1 else " days", cur_c,
                  rng_text(s["cur_rng"]), f"Current streak: {s['cur_len']} consecutive days with contributions", 0.00))
    o.append(card(x2, ys[0], cw, ch, "LONGEST STREAK", s["long_len"], 0, " day" if s["long_len"] == 1 else " days", TXT,
                  rng_text(s["long_rng"]), f"Longest streak in period: {s['long_len']} days", 0.15))
    o.append(card(x1, ys[1], cw, ch, "CONTRIBUTIONS", s["total"], 0, "", TXT,
                  "in the last 12 months", f"{s['total']:,} contributions, {span_txt}", 0.30))
    o.append(card(x2, ys[1], cw, ch, "ACTIVE DAYS", s["active"], 0, f" / {s['span']}", TXT,
                  f"{pct}% of days active", f"{s['active']} of {s['span']} days had at least one contribution", 0.45))
    o.append(card(x1, ys[2], cw, ch, "BEST DAY", s["best_count"], 0, "", TXT,
                  fmt_d(s["best_date"]), f"Highest single day: {s['best_count']} contributions on {s['best_date'].isoformat()}", 0.60))
    o.append(card(x2, ys[2], cw, ch, "AVG / ACTIVE DAY", s["avg"], 1, "", TXT,
                  "contributions", f"{s['total']:,} contributions ÷ {s['active']} active days", 0.75))

    # strip: repos / stars / highest activity
    sy, sh = 428, 76
    def mini(x, w, label, value, tip, delay):
        body = num(x + 24, sy + 56, value, 0, "", TXT, delay + 0.3, size=30)
        extra = ""
        if value is None:
            tip += " (unavailable: set GH_TOKEN to enable)"
        return (
            f'<g class="t card" style="animation-delay:{delay:.2f}s"><title>{esc(tip)}</title>'
            f'<rect x="{x}" y="{sy}" width="{w}" height="{sh}" rx="10" fill="{CARD}" stroke="{BORDER}"/>'
            f'<text x="{x + 24}" y="{sy + 26}" fill="{MUTED}" font-size="13" letter-spacing="1.5">{label}</text>{body}{extra}</g>'
        )
    o.append(mini(20, 190, "PUBLIC REPOS", repos, "Public repositories owned by the user", 0.90))
    o.append(mini(222, 190, "TOTAL STARS", stars, "Stars across public repositories owned by the user", 1.00))
    o.append(
        f'<g class="t card" style="animation-delay:1.10s"><title>Highest activity: {fmt_d(s["best_date"])} with {s["best_count"]} contributions; '
        f'peak month {esc(peak_name)} with {peak_val:,}</title>'
        f'<rect x="424" y="{sy}" width="396" height="{sh}" rx="10" fill="{CARD}" stroke="{GREEN_D}"/>'
        f'<text x="448" y="{sy + 26}" fill="{GREEN}" font-size="13" letter-spacing="1.5">HIGHEST ACTIVITY</text>'
        f'<text x="448" y="{sy + 49}" fill="{TXT}" font-size="20" font-weight="700">{fmt_d(s["best_date"])} · {s["best_count"]} contributions</text>'
        f'<text x="448" y="{sy + 67}" fill="{MUTED}" font-size="13">peak month: {esc(peak_name)} · {peak_val:,}</text></g>'
    )

    # chart
    cy, chh = 516, 284
    o.append(f'<g class="t" style="animation-delay:1.20s"><rect x="20" y="{cy}" width="800" height="{chh}" rx="10" fill="{CARD}" stroke="{BORDER}"/>'
             f'<text x="44" y="{cy + 34}" fill="{MUTED}" font-size="15" letter-spacing="1.5">CONTRIBUTIONS / MONTH</text></g>')
    base, maxh, x0, area = cy + 242, 140, 44, 752
    slot = area / len(months)
    bw = min(36, slot * 0.62)
    mx = max(v for _, v in months) or 1
    for i, ((yy, mm), v) in enumerate(months):
        bx = x0 + i * slot + (slot - bw) / 2
        cxm = bx + bw / 2
        h = max(2.0, maxh * v / mx) if v > 0 else 2.0
        top = base - h
        is_peak = (yy, mm) == peak_key
        delay = 1.45 + i * 0.06
        name = dt.date(yy, mm, 1).strftime("%b")
        full = dt.date(yy, mm, 1).strftime("%B %Y")
        fill = GREEN if is_peak else GREEN_D
        flt = ' filter="url(#glow)"' if is_peak else ""
        o.append(
            f'<g class="bar"><title>{esc(full)}: {v:,} contributions</title>'
            f'<rect class="b" x="{bx:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="3" fill="{fill}"{flt} style="animation-delay:{delay:.2f}s"/>'
            f'<text class="t" style="animation-delay:{delay + 0.6:.2f}s" x="{cxm:.1f}" y="{top - 8:.1f}" fill="{GREEN if is_peak else TXT}" '
            f'font-size="{14 if is_peak else 13}" font-weight="{700 if is_peak else 400}" text-anchor="middle">{v:,}</text></g>'
        )
        o.append(f'<text x="{cxm:.1f}" y="{base + 20}" fill="{TXT if is_peak else MUTED}" font-size="13" text-anchor="middle">{name}</text>')
        if i == 0 or mm == 1 or i == len(months) - 1:
            o.append(f'<text x="{cxm:.1f}" y="{base + 33}" fill="{MUTED}" font-size="10" text-anchor="middle" opacity=".7">{yy}</text>')

    # footer: identity
    fy = 812
    o.append(f'<rect x="20" y="{fy}" width="800" height="50" rx="10" fill="{CARD}" stroke="{BORDER}"/>')
    o.append(f'<text x="44" y="{fy + 31}" fill="{GREEN}" font-size="15" font-weight="700">$ data_analyst</text>')
    cx = 196
    for sk in SKILLS:
        w = len(sk) * 7.9 + 22
        o.append(f'<rect x="{cx:.1f}" y="{fy + 13}" width="{w:.1f}" height="24" rx="12" fill="{BG2}" stroke="{BORDER}"/>'
                 f'<text x="{cx + w / 2:.1f}" y="{fy + 29}" fill="{TXT}" font-size="13" text-anchor="middle">{esc(sk)}</text>')
        cx += w + 8
    o.append("</svg>")
    return "".join(o)


def main():
    days, repos, stars, source = collect()
    s = compute_stats(days)
    svg = render_svg(s, repos, stars, source)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {OUT} | {s['total']} contributions, {s['active']}/{s['span']} active days, "
          f"streak {s['cur_len']} (longest {s['long_len']}), repos={repos}, stars={stars}, source={source}")


if __name__ == "__main__":
    main()
