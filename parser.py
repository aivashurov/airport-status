#!/usr/bin/env python3
"""
Парсер
— пробует 3 разных RSS‑URL, берёт первый «живой»
— пишет понятные сообщения в лог
— если всё упало → оставляет старые данные и не заливает «пустой» сайт
"""

import feedparser, re, json, html, unicodedata, sys
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

CHANNEL = "korenyako"
BASE1   = "https://wtf.roflcopter.fr/rss-bridge/?action=display&bridge=Telegram"
BASE2   = "https://rss-bridge.org/bridge01/?action=display&bridge=Telegram"

FEEDS = [
    # tg.i‑c‑a.su даёт ~30‑40 последних постов
    "https://tg.i-c-a.su/rss/korenyako",

    # старые источники оставляем резервом
    f"{BASE1}&username={CHANNEL}&format=Atom&n=100",
    f"{BASE1}&username={CHANNEL}&format=Atom",
    f"{BASE2}&username={CHANNEL}&format=Atom",
]

# ─── классификация open/closed ────────────────────────────────────────────────
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|снят\w[^.]{0,120}?ограничен\w+|"
    r"возобновил\w[^.]{0,120}?при[её]м)", re.I | re.S)
RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}?введ\w+)", re.I | re.S)

# ─── ICAO справочник + гибкие паттерны имён ───────────────────────────────────
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL",
}
def make_regex(name):  # «Внуково» → \bВнуков\w*
    stem = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", name, flags=re.I)
    if len(stem) < 4:
        stem = name
    return re.compile(rf"\b{re.escape(stem)}\w*", re.I | re.U)
NAME_RE = {icao: make_regex(name) for name, icao in ICAO_MAP.items()}
RX_CODE = re.compile(r"(?P<name>[А-ЯЁ\w\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)")

# ─── утилиты ──────────────────────────────────────────────────────────────────
def normalize(t): return unicodedata.normalize("NFC", html.unescape(t))

def full_text(entry):
    out = []
    for key in ("title", "summary", "description"):
        v = entry.get(key)
        if isinstance(v, str):
            out.append(v)
    content = entry.get("content")
    if isinstance(content, list):
        out += [c.get("value", "") for c in content if isinstance(c, dict)]
    elif isinstance(content, dict):
        out.append(content.get("value", ""))
    elif isinstance(content, str):
        out.append(content)
    return normalize("\n".join(out))

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

# ─── история + сайт ───────────────────────────────────────────────────────────
def load_hist():
    try:    return json.loads(Path("status.json").read_text())
    except: return {}

def save_hist(h): Path("status.json").write_text(json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    if not hist:
        Path("index.html").write_text("<h1>Нет данных из канала — попробуйте позже</h1>")
        Path("history.html").write_text("<h1>История пуста</h1>")
        return
    for page in ("index.html", "history.html"):
        tpl = Template(Path(f"templates/{page}").read_text())
        Path(page).write_text(tpl.render(airports=hist))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def fetch_entries():
    for idx, url in enumerate(FEEDS, 1):
        print(f"=== Trying feed {idx}/{len(FEEDS)} : {url}")
        fp = feedparser.parse(url)
        if fp.bozo:
            print("   ⚠️  bozo:", fp.bozo_exception, file=sys.stderr)
        if fp.get("status") and fp.status >= 400:
            print(f"   ❌ HTTP {fp.status}", file=sys.stderr)
            continue
        if len(fp.entries) == 0:
            print("   ⚠️  0 entries, trying next")
            continue
        print(f"   ✅ OK, got {len(fp.entries)} entries")
        return fp.entries
    print("No entries in any feed", file=sys.stderr)
    return []

def process():
    hist = load_hist()
    entries = fetch_entries()
    if not entries:                      # ничего нового — оставляем старые данные
        build_site(hist)
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
