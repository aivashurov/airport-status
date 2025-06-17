#!/usr/bin/env python3
"""
GitHub Pages
Версия 2025‑06‑17‑fix: безопасно собираем текст, не падаем,
и показываем «данных нет» если фид пустой.
"""

import feedparser, re, json, html, unicodedata, sys
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

FEED_LIMIT = 100
FEED_URL = (f"https://wtf.roflcopter.fr/rss-bridge/?action=display"
            f"&bridge=Telegram&username=korenyako&format=Atom&n={FEED_LIMIT}")

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

def make_name_regex(name: str) -> re.Pattern:
    base = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", name, flags=re.I)  # обрезаем фин. гласную
    if len(base) < 4:
        base = name
    return re.compile(rf"\b{re.escape(base)}\w*", re.I | re.U)

NAME_RE = {icao: make_name_regex(name) for name, icao in ICAO_MAP.items()}

RX_CODE = re.compile(
    r"(?P<name>[А-ЯЁA-Za-z\u2013\u2014\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)",
    re.U)

# ───────────── 3. Утилиты ────────────────────────────────────────────────────
def normalize(txt: str) -> str:
    return unicodedata.normalize("NFC", html.unescape(txt))

def classify(text: str):
    if RX_CLOSED.search(text):
        return "closed"
    if RX_OPEN.search(text):
        return "open"

def extract_airports(text: str):
    found = {}

    # (1) Имя + код
    for m in RX_CODE.finditer(text):
        name, icao = m.group("name").strip(), m.group("icao")
        found[icao] = name
        ICAO_MAP.setdefault(name, icao)
        NAME_RE.setdefault(icao, make_name_regex(name))

    # (2) Голые имена
    for icao, pat in NAME_RE.items():
        if pat.search(text):
            found[icao] = next(n for n, i in ICAO_MAP.items() if i == icao)

    return found.items()

def safe_full_text(entry) -> str:
    """Собираем все возможные поля, не падаем, если их нет/другого типа."""
    parts = []
    for key in ("title", "summary", "description"):
        val = entry.get(key)
        if isinstance(val, str):
            parts.append(val)
    # content может быть list, dict, str или отсутствовать
    content = entry.get("content")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                parts.append(c.get("value", ""))
            elif isinstance(c, str):
                parts.append(c)
    elif isinstance(content, dict):
        parts.append(content.get("value", ""))
    elif isinstance(content, str):
        parts.append(content)
    return normalize("\n".join(parts))

# ───────────── 4. Хранилище и сайт ───────────────────────────────────────────
def load_hist():
    try:
        return json.loads(Path("status.json").read_text())
    except FileNotFoundError:
        return {}

def save_hist(h): Path("status.json").write_text(
        json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    # если данных нет – простая страничка‑заглушка
    if not hist:
        Path("index.html").write_text(
            "<h1>Нет данных из канала – проверьте workflow лог</h1>")
        Path("history.html").write_text("<h1>История пуста</h1>")
        return
    for page in ("index.html", "history.html"):
        tpl = Template(Path(f"templates/{page}").read_text())
        Path(page).write_text(tpl.render(airports=hist))

# ───────────── 5. MAIN ───────────────────────────────────────────────────────
def process():
    hist = load_hist()
    try:
        feed = feedparser.parse(FEED_URL)["entries"]
    except Exception as e:
        print("Feed error:", e, file=sys.stderr)
        build_site(hist)  # показываем, что уже было
        return

    for e in sorted(feed, key=lambda x: x["published_parsed"]):
        text = safe_full_text(e)
        status = classify(text)
        if not status:
            continue
        ts = dparse.parse(e["published"]).astimezone(timezone.utc).isoformat()
        for icao, name in extract_airports(text):
            ap = hist.setdefault(icao, {"name": name, "events": []})
            ap["events"].append({"ts": ts, "status": status})
            ap["current"] = status

    save_hist(hist)
    build_site(hist)

if __name__ == "__main__":
    process()
