#!/usr/bin/env python3
"""
Парсер TG‑канала → GitHub Pages
 • работает, даже если в сообщении нет ICAO
 • даты храним ISO‑строкой → JSON‑safe
 • выводим время в зоне Europe/Moscow (+03)
 • считаем длительность каждого периода ограничений
 • сортировка событий теперь по ID поста (надёжнее, чем published)
2025‑06‑17 (MSK REV 3)
"""

import feedparser, re, json, html, unicodedata, itertools, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

MSK = ZoneInfo("Europe/Moscow")
FEED_URL = "https://tg.i-c-a.su/rss/korenyako?limit=100"

# ───── 1. Ключевые фразы ─────────────────────────────────────────────────────
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|"
    r"снят\w[^.]{0,120}?ограничен\w+|"
    r"возобновил\w[^.]{0,120}?при[её]м)",
    re.I | re.S)
RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}?введ\w+)",
    re.I | re.S)

# ───── 2. Словарь «имя → ICAO» (можно дополнять) ─────────────────────────────
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL", "Калуга": "UUBC",
}

def slug(name: str) -> str:
    return unicodedata.normalize("NFKD", name).lower().replace('ё', 'е')

def make_regex(name):
    stem = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", name, flags=re.I)
    if len(stem) < 4:
        stem = name
    return re.compile(rf"\b{re.escape(stem)}\w*", re.I | re.U)

NAME_RE = {icao: make_regex(n) for n, icao in ICAO_MAP.items()}
RX_CODE = re.compile(r"(?P<name>[А-ЯЁ\w\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)")

# ───── 3. Вспомогательные ────────────────────────────────────────────────────
def normalize(t): return unicodedata.normalize("NFC", html.unescape(t))

def full_text(e):
    parts = [e.get(k, "") for k in ("title", "summary", "description")]
    content = e.get("content")
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

def extract_airports(txt):
    found = []
    # (1) Имя + код
    for m in RX_CODE.finditer(txt):
        name, icao = m["name"].strip(), m["icao"]
        found.append((icao, name, icao))
        ICAO_MAP.setdefault(name, icao)
        NAME_RE.setdefault(icao, make_regex(name))
    # (2) Голые названия
    for name in itertools.chain(ICAO_MAP.keys(), [f[1] for f in found]):
        pat = NAME_RE.get(ICAO_MAP.get(name)) or make_regex(name)
        if pat.search(txt):
            icao = ICAO_MAP.get(name)
            key = icao if icao else slug(name)
            found.append((key, name, icao))
    # уникализируем
    uniq = {}
    for k, n, i in found:
        uniq[k] = (k, n, i)
    return list(uniq.values())

def fmt(dt: datetime) -> str: return dt.strftime("%d.%m.%Y %H:%M") + " (MSK)"

def duration_str(td: timedelta) -> str:
    h, rem = divmod(td.total_seconds(), 3600)
    m = int(rem // 60)
    if h >= 24:
        d, h = divmod(int(h), 24)
        return f"{d} д {h} ч {m} м"
    return f"{int(h)} ч {m} м"

# ───── 4. I/O (ISO строки) ───────────────────────────────────────────────────
def load_hist():
    try:
        raw = json.loads(Path("status.json").read_text())
        for ap in raw.values():
            for ev in ap["events"]:
                ev["dt"] = datetime.fromisoformat(ev["dt"]).astimezone(MSK)
        return raw
    except FileNotFoundError:
        return {}

def save_hist(hist):
    serial = {}
    for k, ap in hist.items():
        serial[k] = {
            "name": ap["name"],
            "icao": ap.get("icao"),
            "current": ap["current"],
            "events": [
                {"dt": ev["dt"].isoformat(), "status": ev["status"]}
                for ev in ap["events"]
            ],
        }
    Path("status.json").write_text(json.dumps(serial, ensure_ascii=False, indent=2))

# ───── 5. Периоды ограничений ────────────────────────────────────────────────
def recompute_periods(ap):
    periods, start = [], None
    for ev in ap["events"]:
        if ev["status"] == "closed" and start is None:
            start = ev["dt"]
        if ev["status"] == "open" and start:
            periods.append({
                "from": fmt(start),
                "to": fmt(ev["dt"]),
                "dur": duration_str(ev["dt"] - start)
            })
            start = None
    if start:
        periods.append({
            "from": fmt(start),
            "to": "— идёт",
            "dur": duration_str(datetime.now(MSK) - start)
        })
    ap["periods"] = periods

def build_site(hist):
    if not hist:
        for p in ("index.html", "history.html"):
            Path(p).write_text("<h1>Нет данных — попробуйте позже</h1>")
        return
    for ap in hist.values():
        recompute_periods(ap)
    tpl_idx = Template(Path("templates/index.html").read_text())
    tpl_his = Template(Path("templates/history.html").read_text())
    Path("index.html").write_text(tpl_idx.render(airports=hist))
    Path("history.html").write_text(tpl_his.render(airports=hist))

def merge_slug(hist, slug_key, icao_key):
    merged = sorted(
        hist[slug_key]["events"] + hist[icao_key]["events"],
        key=lambda x: x["dt"]
    )
    hist[icao_key]["events"] = merged
    hist[icao_key]["current"] = merged[-1]["status"]
    del hist[slug_key]

# ───── 6. MAIN ───────────────────────────────────────────────────────────────
def msg_id(item) -> int:
    """Извлекаем числовой ID из link, чтобы сортировать надёжнее pubDate"""
    try:
        return int(str(item.get("link", "")).rstrip("/").split("/")[-1])
    except ValueError:
        return 0

def process():
    hist = load_hist()
    fp = feedparser.parse(FEED_URL)
    if not fp.entries:
        build_site(hist); return

    for e in sorted(fp.entries, key=msg_id):
        txt = full_text(e)
        status = classify(txt)
        if not status:
            continue
        dt_msk = dparse.parse(e["published"]).astimezone(MSK)

        for key, name, icao in extract_airports(txt):
            if icao and icao in hist and slug(name) in hist:
                merge_slug(hist, slug(name), icao)

            rec = hist.setdefault(key, {"name": name, "icao": icao, "events": []})
            if not rec.get("icao") and icao:
                rec["icao"] = icao
                if key != icao:
                    hist[icao] = rec; del hist[key]; key = icao
            rec["events"].append({"dt": dt_msk, "status": status})
            rec["current"] = status

    save_hist(hist)
    build_site(hist)

if __name__ == "__main__":
    process()
