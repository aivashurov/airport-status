#!/usr/bin/env python3
import feedparser, re, json, html, unicodedata
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

FEED_URL = ("https://wtf.roflcopter.fr/rss-bridge/?action=display"
            "&bridge=Telegram&username=korenyako&format=Atom")

# ─── 1. КЛЮЧЕВЫЕ ФРАЗЫ ──────────────────────────────────────────────────────────
RX_OPEN  = re.compile(
    r"(ограничен\w+\s+снят\w*|снят\w+\s+ограничен\w+|"
    r"возобновил\w+[^.]{0,40}приём|возобновил\w+[^.]{0,40}прием)",
    re.I | re.S)

RX_CLOSED = re.compile(
    r"(временн\w+\s+ограничен\w+[^.]{0,40}(?:введ|ввод)|"
    r"➕)",   # в некоторых постах ставят «➕» у закрытых аэропортов
    re.I | re.S)

# ─── 2. АЭРОПОРТЫ ───────────────────────────────────────────────────────────────
#   • (Внуково; UUWW)  • Внуково (код ИКАО: UUWW)  • Внуково
RX_AP_WITH_CODE = re.compile(
    r"(?P<name>[А-ЯЁA-Za-z\u2013\u2014\s-]+?)\s*\([^)]*?"
    r"(?P<icao>[A-Z]{4})\)", re.U)

RX_AP_NAME_ONLY = re.compile(
    r"\b(Внуково|Домодедово|Шереметьево|Жуковский|Пулково|Казань|"
    r"Тамбов|Нижний\sНовгород|Ярославль|Владимир|Нижнекамск"
    r")\b", re.U | re.I)

# Справочник «Имя → ICAO». Можно дополнять по мере появления новых аэропортов.
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Тамбов": "UUOT", "Нижний Новгород": "UWGG", "Ярославль": "UUDL",
    "Владимир": "UUBY", "Нижнекамск": "UWKE",
}

# ─── 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ─────────────────────────────────────────────────
def normalize(txt: str) -> str:
    """Снимаем юникод‑диакритику и приводим к NFC, чтобы сравнивать честно."""
    return unicodedata.normalize("NFC", txt)

def classify(text: str):
    if RX_CLOSED.search(text):
        return "closed"
    if RX_OPEN.search(text):
        return "open"

def extract_airports(text: str):
    seen = {}
    # 1) варианты со скобками + кодом
    for m in RX_AP_WITH_CODE.finditer(text):
        name, icao = m.group("name").strip(), m.group("icao")
        seen[icao] = name
    # 2) «голые» названия
    for m in RX_AP_NAME_ONLY.finditer(text):
        name = m.group(1).strip()
        if name in ICAO_MAP:
            seen[ICAO_MAP[name]] = name
    return seen.items()        # [(icao, name), …]

def load_hist():
    try:
        return json.loads(Path("status.json").read_text())
    except FileNotFoundError:
        return {}

def save_hist(h): Path("status.json").write_text(
        json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    for page in ("index.html", "history.html"):
        tmpl = Template(Path(f"templates/{page}").read_text())
        Path(page).write_text(tmpl.render(airports=hist))

# ─── 4. ОСНОВНОЙ ПРОЦЕСС ────────────────────────────────────────────────────────
def process():
    hist = load_hist()
    feed = feedparser.parse(FEED_URL)["entries"]
    for e in sorted(feed, key=lambda x: x["published_parsed"]):
        raw = html.unescape(e.get("title", "") + "\n" + e.get("summary", ""))
        text = normalize(raw)
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
