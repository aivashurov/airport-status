#!/usr/bin/env python3
"""
Парсер 
— читает единственный RSS‑фид
— обновляет status.json и генерирует index.html + history.html
2025‑06‑17 (LITE ONE‑FEED REVISION)
"""

import feedparser, re, json, html, unicodedata, sys
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

FEED_URL = "https://tg.i-c-a.su/rss/korenyako"

# ───────────── 1. Классификация «open / closed» ──────────────────────────────
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|"
    r"снят\w[^.]{0,120}?ограничен\w+|"
    r"возобновил\w[^.]{0,120}?при[её]м)",
    re.I | re.S)

RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}?введ\w+)",
    re.I | re.S)

# ───────────── 2. ICAO‑справочник + гибкие паттерны имён ─────────────────────
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL",
}

def make_regex(name):
    stem = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", name, flags=re.I)
    if len(stem) < 4:
        stem = name
    return re.compile(rf"\b{re.escape(stem)}\w*", re.I | re.U)

NAME_RE = {icao: make_regex(name) for name, icao in ICAO_MAP.items()}

RX_CODE = re.compile(r"(?P<name>[А-ЯЁ\w\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)")

# ───────────── 3. Утилиты ────────────────────────────────────────────────────
def normalize(txt): return unicodedata.normalize("NFC", html.unescape(txt))

def full_text(entry):
    parts = []
    for key in ("title", "summary", "description"):
        v = entry.get(key)
        if isinstance(v, str):
            parts.append(v)
    content = entry.get("content")
    if isinstance(content, list):
        parts += [c.get("value", "") for c in content if isinstance(c, dict)]
    elif isinstance(content, dict):
        parts.append(content.get("value", ""))
    elif isinstance(content, str):
        parts.append(content)
    return normalize("\n".join(parts))

def classify(txt):
    if RX_CLOSED.search(txt):
        return "closed"
    if RX_OPEN.search(txt):
        return "open"

def find_airports(txt):
    found = {}
    for m in RX_CODE.finditer(txt):
        name, icao = m["name"].strip(), m["icao"]
        found[icao] = name
        ICAO_MAP.setdefault(name, icao)
        NAME_RE.setdefault(icao, make_regex(name))
    for icao, pat in NAME_RE.items():
        if pat.search(txt):
            found[icao] = next(n for n, i in ICAO_MAP.items() if i == icao)
    return found.items()

# ───────────── 4. История + сайт ─────────────────────────────────────────────
def load_hist():
    try:
        return json.loads(Path("status.json").read_text())
    except FileNotFoundError:
        return {}

def save_hist(h):
    Path("status.json").write_text(json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    if not hist:
        Path("index.html").write_text("<h1>Нет данных из канала — попробуйте позже</h1>")
        Path("history.html").write_text("<h1>История пуста</h1>")
        return
    for page in ("index.html", "history.html"):
        tpl = Template(Path(f"templates/{page}").read_text())
        Path(page).write_text(tpl.render(airports=hist))

# ───────────── 5. MAIN ───────────────────────────────────────────────────────
def process():
    hist = load_hist()

    print(f"Fetching feed: {FEED_URL}")
    fp = feedparser.parse(FEED_URL)
    if fp.bozo:
        print("   ⚠️  bozo:", fp.bozo_exception, file=sys.stderr)
    if fp.get("status") and fp.status >= 400:
        print(f"   ❌ HTTP {fp.status}", file=sys.stderr)

    entries = fp.entries
    if not entries:
        print("No entries in the feed", file=sys.stderr)
        build_site(hist)   # показываем, что было
        return

    for e in sorted(entries, key=lambda x: x.get("published_parsed")):
        txt = full_text(e)
        status = classify(txt)
        if not status:
            continue
        ts = dparse.parse(e["published"]).astimezone(timezone.utc).isoformat()
        for icao, name in find_airports(txt):
            ap = hist.setdefault(icao, {"name": name, "events": []})
            ap["events"].append({"ts": ts, "status": status})
            ap["current"] = status

    save_hist(hist)
    build_site(hist)

if __name__ == "__main__":
    process()
