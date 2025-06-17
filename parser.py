#!/usr/bin/env python3
"""
Версия 2025‑06‑17: поддержка падежей + расширенный фид (до 100 постов).
"""

import feedparser, re, json, html, unicodedata
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

FEED_LIMIT = 100   # сколько последних сообщений брать из канала
FEED_URL = (f"https://wtf.roflcopter.fr/rss-bridge/?action=display"
            f"&bridge=Telegram&username=korenyako&format=Atom&n={FEED_LIMIT}")

# ────────────────────── 1.  Классификация событий ────────────────────────────
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|"      # «ограничения … сняты»
    r"снят\w[^.]{0,120}?ограничен\w+|"       # «сняты … ограничения»
    r"возобновил\w[^.]{0,120}?при[её]м)",    # «возобновили приём»
    re.I | re.S)

RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}?введ\w+)",
    re.I | re.S)

# ───────────────────────── 2.  Справочник ICAO ───────────────────────────────
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL",
}

# ───────────────────── 3.  Морф‑паттерны для имён ────────────────────────────
def make_name_pattern(name: str) -> re.Pattern:
    """
    «Жуковский» → \bЖуков\w*  (ловит Жуковский, Жуковского, Жуковском…)
    «Внуково»    → \bВнуков\w*
    """
    base = name
    lowers = name.lower()

    # обрезаем типичные окончания
    for suf in ("ский", "цкий", "кий", "ый", "ий", "ой", "ево", "ово", "ево"):
        if lowers.endswith(suf):
            base = name[:-len(suf)]
            break
    # если слово заканчивается на гласную, убираем её
    base = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", base, flags=re.I)
    # минимальная длина корня 4 символа, иначе оставляем оригинал
    if len(base) < 4:
        base = name
    return re.compile(rf"\b{re.escape(base)}\w*", re.I | re.U)

NAME_PATTERNS = {icao: make_name_pattern(name) for name, icao in ICAO_MAP.items()}

RX_CODE = re.compile(
    r"(?P<name>[А-ЯЁA-Za-z\u2013\u2014\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)",
    re.U)

# ───────────────────────── 4.  Вспомогательные ───────────────────────────────
def normalize(txt: str) -> str:
    return unicodedata.normalize("NFC", html.unescape(txt))

def classify(text: str):
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
        ICAO_MAP.setdefault(name, icao)
        NAME_PATTERNS.setdefault(icao, make_name_pattern(name))

    # (б) Голые названия (любые, которые уже есть в словаре)
    for icao, pat in NAME_PATTERNS.items():
        if pat.search(text):
            found[icao] = next(n for n, i in ICAO_MAP.items() if i == icao)

    return found.items()

def load_hist():
    try:
        return json.loads(Path("status.json").read_text())
    except FileNotFoundError:
        return {}

def save_hist(h):
    Path("status.json").write_text(json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    for page in ("index.html", "history.html"):
        tpl = Template(Path(f"templates/{page}").read_text())
        Path(page).write_text(tpl.render(airports=hist))

# ────────────────────────────── 5.  MAIN ─────────────────────────────────────
def process():
    hist = load_hist()
    feed = feedparser.parse(FEED_URL)["entries"]

    # старое → новое (чтобы «последнее» событие перезаписывалось)
    for e in sorted(feed, key=lambda x: x["published_parsed"]):
        text_parts = [e.get("title", ""), e.get("summary", "")]
        text_parts += [c.get("value", "") for c in e.get("content", [])]
        text = normalize("\n".join(text_parts))

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
