#!/usr/bin/env python3
"""
Парсер
• выводит даты уже в зоне Europe/Moscow (UTC+3)
• считает длительность каждого периода ограничений
2025‑06‑17 (MSK‑DURATION REVISION)
"""

import feedparser, re, json, html, unicodedata, sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template
import itertools

MSK = ZoneInfo("Europe/Moscow")           # постоянный +03:00

FEED_URL = "https://tg.i-c-a.su/rss/korenyako"

# ─────── 1.  Регэкспы «open / closed» ─────────────────────────────────────────
RX_OPEN = re.compile(
    r"(ограничен\w[^.]{0,120}?снят\w+|"
    r"снят\w[^.]{0,120}?ограничен\w+|"
    r"возобновил\w[^.]{0,120}?при[её]м)",
    re.I | re.S)
RX_CLOSED = re.compile(
    r"(временн\w[^.]{0,120}?ограничен\w[^.]{0,120}?введ\w+)",
    re.I | re.S)

# ─────── 2.  Справочник имя → ICAO ───────────────────────────────────────────
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL", "Калуга": "UUBC",
}

def slug(name):  # для временных записей без кода
    return unicodedata.normalize("NFKD", name).lower().replace('ё', 'е')

def make_regex(name):
    stem = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", name, flags=re.I)
    if len(stem) < 4:
        stem = name
    return re.compile(rf"\b{re.escape(stem)}\w*", re.I | re.U)

NAME_RE = {icao: make_regex(n) for n, icao in ICAO_MAP.items()}
RX_CODE = re.compile(r"(?P<name>[А-ЯЁ\w\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)")

# ─────── 3.  Служебные ────────────────────────────────────────────────────────
def normalize(txt): return unicodedata.normalize("NFC", html.unescape(txt))

def full_text(e):
    parts = [e.get("title", ""), e.get("summary", ""), e.get("description", "")]
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

    # уникализируем по ключу
    uniq = {}
    for k, n, i in found:
        uniq[k] = (k, n, i)
    return list(uniq.values())

def fmt(dt: datetime) -> str:             # 17.06.2025 15:18 (MSK)
    return dt.strftime("%d.%m.%Y %H:%M") + " (MSK)"

def load_hist():
    try:    return json.loads(Path("status.json").read_text())
    except: return {}

def save_hist(h): Path("status.json").write_text(json.dumps(h, ensure_ascii=False, indent=2))

def duration_str(td: timedelta) -> str:
    h, rem = divmod(td.total_seconds(), 3600)
    m = rem // 60
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{int(d)} д {int(h)} ч {int(m)} м"
    return f"{int(h)} ч {int(m)} м"

def recompute_periods(ap):
    """строим список {'from','to','dur'} для истории"""
    periods = []
    current_start = None
    for ev in ap["events"]:
        if ev["status"] == "closed" and current_start is None:
            current_start = ev["dt"]
        if ev["status"] == "open" and current_start:
            periods.append({
                "from": fmt(current_start),
                "to":   fmt(ev["dt"]),
                "dur":  duration_str(ev["dt"] - current_start)
            })
            current_start = None
    # ограничения ещё действуют
    if current_start:
        periods.append({
            "from": fmt(current_start),
            "to":   "— идёт",
            "dur":  duration_str(datetime.now(MSK) - current_start)
        })
    ap["periods"] = periods

def build_site(hist):
    if not hist:
        Path("index.html").write_text("<h1>Нет данных — попробуйте позже</h1>")
        Path("history.html").write_text("<h1>История пуста</h1>")
        return
    for ap in hist.values():
        recompute_periods(ap)

    tpl_idx = Template(Path("templates/index.html").read_text())
    tpl_his = Template(Path("templates/history.html").read_text())
    Path("index.html").write_text(tpl_idx.render(airports=hist))
    Path("history.html").write_text(tpl_his.render(airports=hist))

def merge_slug(hist, slug_key, icao_key):
    slug_ev = hist[slug_key]["events"]
    icao_ev = hist[icao_key]["events"]
    merged = sorted(slug_ev + icao_ev, key=lambda x: x["dt"])
    hist[icao_key]["events"] = merged
    hist[icao_key]["current"] = merged[-1]["status"]
    del hist[slug_key]

# ─────── MAIN ────────────────────────────────────────────────────────────────
def process():
    hist = load_hist()
    fp = feedparser.parse(FEED_URL)
    if not fp.entries:
        build_site(hist); return

    for e in sorted(fp.entries, key=lambda x: x.get("published_parsed")):
        txt = full_text(e)
        status = classify(txt)
        if not status:
            continue
        dt_msk = dparse.parse(e["published"]).astimezone(MSK)

        for key, name, icao in extract_airports(txt):
            # слияние slug→icao
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
