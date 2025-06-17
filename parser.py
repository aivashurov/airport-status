#!/usr/bin/env python3
"""
Парсер для GitHub Pages
— берёт полный текст (content) из RSS‑Bridge
— надёжно классифицирует «введены / сняты / возобновили»
— динамически ищет «голые» названия аэропортов по уже известному ICAO‑словарю
"""

import feedparser, re, json, html, unicodedata
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

FEED_URL = ("https://wtf.roflcopter.fr/rss-bridge/?action=display"
            "&bridge=Telegram&username=korenyako&format=Atom")

# ─────────────────── 1. Классификация событий ────────────────────────────────
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|"      # «ограничения … сняты»
    r"снят\w[^.]{0,120}?ограничен\w+|"       # «сняты … ограничения»
    r"возобновил\w[^.]{0,120}?при[её]м)",    # «возобновили приём»
    re.I | re.S)

RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}?введ\w+)",  # «временные ограничения … введены / вводятся»
    re.I | re.S)

# ───────────────────── 2. Поиск аэропортов ───────────────────────────────────
RX_CODE = re.compile(
    r"(?P<name>[А-ЯЁA-Za-z\u2013\u2014\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)",
    re.U)

# стартовый словарь; пополняем из сообщений с кодами
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL",
}

def normalize(txt: str) -> str:
    return unicodedata.normalize("NFC", html.unescape(txt))

def classify(text: str) -> str | None:
    if RX_CLOSED.search(text):
        return "closed"
    if RX_OPEN.search(text):
        return "open"

def find_airports(text: str):
    found = {}

    # (а) Имя + код
    for m in RX_CODE.finditer(text):
        name, icao = m.group("name").strip(), m.group("icao")
        found[icao] = name
        ICAO_MAP.setdefault(name, icao)          # пополняем словарь

    # (б) «Голое» имя из уже известного словаря
    for name, icao in ICAO_MAP.items():
        if re.search(rf"\b{re.escape(name)}\b", text, re.I | re.U):
            found[icao] = name

    return found.items()

# ─────────────────── 3. История и сайт ───────────────────────────────────────
def load_hist():
    try:
        return json.loads(Path("status.json").read_text())
    except FileNotFoundError:
        return {}

def save_hist(h): Path("status.json").write_text(
        json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    for page in ("index.html", "history.html"):
        tpl = Template(Path(f"templates/{page}").read_text())
        Path(page).write_text(tpl.render(airports=hist))

# ───────────────────────────── 4. MAIN ────────────────────────────────────────
def process():
    hist = load_hist()
    feed = feedparser.parse(FEED_URL)["entries"]

    # сортируем старое → новое, чтобы последний встретившийся статус стал «current»
    for e in sorted(feed, key=lambda x: x["published_parsed"]):
        parts = [e.get("title", ""), e.get("summary", "")]
        for c in e.get("content", []):
            parts.append(c.get("value", ""))
        text = normalize("\n".join(parts))

        status = classify(text)
        if not status:
            continue

        ts = dparse.parse(e["published"]).astimezone(timezone.utc).isoformat()
        for icao, name in find_airports(text):
            ap = hist.setdefault(icao, {"name": name, "events": []})
            ap["events"].append({"ts": ts, "status": status})
            ap["current"] = status

    save_hist(hist)
    build_site(hist)

if __name__ == "__main__":
    process()
