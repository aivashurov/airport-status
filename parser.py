#!/usr/bin/env python3
"""
Обновлённый парсер
— надёжно ищет «введены / сняты / возобновили»
— понимает названия без ICAO‑кода
— пополняет словарь «имя → код» на лету
"""

import feedparser, re, json, html, unicodedata
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

FEED_URL = ("https://wtf.roflcopter.fr/rss-bridge/?action=display"
            "&bridge=Telegram&username=korenyako&format=Atom")

# ─────────────────────────── 1. КЛЮЧЕВЫЕ ФРАЗЫ ────────────────────────────────
#  • до 120 симв. между «ограничен*» и «снят*» / «введен*»
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|"      # «ограничения … сняты»
    r"снят\w[^.]{0,120}?ограничен\w+|"       # «сняты ограничения»
    r"возобновил\w[^.]{0,120}?при[её]м)",    # «возобновили приём»
    re.I | re.S)

RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}введ\w+|"  # «временные ограничения … введены / вводятся»
    r"➕)",                                                   # маркеры «плюсиков»
    re.I | re.S)

# ───────────────────────────── 2. АЭРОПОРТЫ ───────────────────────────────────
RX_CODE = re.compile(
    r"(?P<name>[А-ЯЁA-Za-z\u2013\u2014\s-]+?)\s*\([^)]*?"
    r"(?P<icao>[A-Z]{4})\)", re.U)

# единая рег‑экспа для «голых» названий (дополнять при новых аэропортах)
RX_NAME = re.compile(
    r"\b(Внуково|Домодедово|Шереметьево|Жуковский|Пулково|Казань|"
    r"Нижний\sНовгород|Тамбов|Ижевск|Саратов|Владимир|Ярославль|"
    r"Нижнекамск)\b", re.I | re.U)

# стартовый словарь — пополняем динамически
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL",
}

# ─────────────────────────── 3. ВСПОМОГАТЕЛЬНОЕ ───────────────────────────────
def normalize(txt: str) -> str:
    """NFC‑нормализация и замена HTML‑сущностей."""
    return unicodedata.normalize("NFC", html.unescape(txt))

def classify(text: str) -> str | None:
    if RX_CLOSED.search(text):
        return "closed"
    if RX_OPEN.search(text):
        return "open"

def find_airports(text: str):
    found = {}

    # (а) «Имя (код)»
    for m in RX_CODE.finditer(text):
        name, icao = m.group("name").strip(), m.group("icao")
        found[icao] = name
        ICAO_MAP.setdefault(name, icao)      # пополняем словарь

    # (б) «Имя» без скобок
    for m in RX_NAME.finditer(text):
        name = m.group(1).strip()
        if name in ICAO_MAP:
            found[ICAO_MAP[name]] = name

    return found.items()                     # [(icao, name), …]

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

# ─────────────────────────────── 4. MAIN ──────────────────────────────────────
def process():
    hist = load_hist()
    entries = feedparser.parse(FEED_URL)["entries"]

    # сортируем от старого к новому, чтобы последние события «перезатирали» ранние
    for e in sorted(entries, key=lambda x: x["published_parsed"]):
        text = normalize(e.get("title", "") + "\n" + e.get("summary", ""))
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
