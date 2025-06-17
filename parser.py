#!/usr/bin/env python3
"""
Парсер — работает даже когда в сообщении нет ICAO‑кода.
Алгоритм:
 • если код найден — ключом записи служит ICAO
 • если кода нет — ключ = slug(name)   (пример: 'калуга')
 • при появлении кода у уже существующего name‑slug:
     ◦ события переносятся,
     ◦ slug‑запись удаляется,
     ◦ словарь ICAO_MAP пополняется.
"""

import feedparser, re, json, html, unicodedata, sys
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template
import itertools

# ─────── 0.  RSS ──────────────────────────────────────────────────────────────
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

# ─────── 2.  Справочник «имя → ICAO»  (можно дополнять вручную) ───────────────
ICAO_MAP = {
    "Внуково": "UUWW", "Домодедово": "UUDD", "Шереметьево": "UUEE",
    "Жуковский": "UUBW", "Пулково": "ULLI", "Казань": "UWKD",
    "Нижний Новгород": "UWGG", "Тамбов": "UUOT", "Ижевск": "USII",
    "Нижнекамск": "UWKE", "Саратов": "UWSG", "Владимир": "UUBY",
    "Ярославль": "UUDL", "Калуга": "UUBC",    # ← добавили Калугу
}

def slug(name: str) -> str:                # «Калуга» → 'калуга'
    return unicodedata.normalize("NFKD", name).lower().replace('ё', 'е')

def make_regex(name):
    stem = re.sub(r"[АОУЫЭЕЁИЮЯ]$", "", name, flags=re.I)
    if len(stem) < 4:
        stem = name
    return re.compile(rf"\b{re.escape(stem)}\w*", re.I | re.U)

NAME_RE = {icao: make_regex(n) for n, icao in ICAO_MAP.items()}
RX_CODE = re.compile(r"(?P<name>[А-ЯЁ\w\s-]+?)\s*\([^)]*?(?P<icao>[A-Z]{4})\)")

# ─────── 3.  Утилиты ──────────────────────────────────────────────────────────
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
    """
    Возвращает список диктов:
      {'key': 'UUBC', 'name': 'Калуга', 'icao': 'UUBC'}
      {'key': 'калуга', 'name': 'Калуга', 'icao': None}
    """
    results = []

    # (1) Имя + код
    for m in RX_CODE.finditer(txt):
        name, icao = m["name"].strip(), m["icao"]
        results.append({"key": icao, "name": name, "icao": icao})
        ICAO_MAP.setdefault(name, icao)
        NAME_RE.setdefault(icao, make_regex(name))

    # (2) Голые имена
    for name in itertools.chain(ICAO_MAP.keys(), [r["name"] for r in results]):
        pat = NAME_RE.get(ICAO_MAP.get(name)) or make_regex(name)
        if pat.search(txt):
            icao = ICAO_MAP.get(name)
            key = icao if icao else slug(name)
            results.append({"key": key, "name": name, "icao": icao})

    # удаляем дубликаты по ключу
    out = {}
    for r in results:
        out[r["key"]] = r
    return list(out.values())

# ─────── 4.  Хранилище + сайт ────────────────────────────────────────────────
def load_hist():
    try:    return json.loads(Path("status.json").read_text())
    except: return {}

def save_hist(h): Path("status.json").write_text(json.dumps(h, ensure_ascii=False, indent=2))

def build_site(hist):
    if not hist:
        for p in ("index.html", "history.html"):
            Path(p).write_text("<h1>Нет данных — попробуйте позже</h1>")
        return
    tpl_idx = Template(Path("templates/index.html").read_text())
    tpl_his = Template(Path("templates/history.html").read_text())
    Path("index.html").write_text(tpl_idx.render(airports=hist))
    Path("history.html").write_text(tpl_his.render(airports=hist))

def merge_slug_into_icao(hist, slug_key, icao_key):
    """Переносим события и удаляем slug‑карточку."""
    slug_ev = hist[slug_key]["events"]
    icao_ev = hist[icao_key]["events"]
    merged = sorted(slug_ev + icao_ev, key=lambda x: x["ts"])
    hist[icao_key]["events"] = merged
    hist[icao_key]["current"] = merged[-1]["status"]
    del hist[slug_key]

# ─────── 5.  MAIN ────────────────────────────────────────────────────────────
def process():
    hist = load_hist()
    fp = feedparser.parse(FEED_URL)
    entries = fp.entries
    if not entries:
        build_site(hist); return

    for e in sorted(entries, key=lambda x: x.get("published_parsed")):
        txt = full_text(e)
        status = classify(txt)
        if not status:
            continue
        ts = dparse.parse(e["published"]).astimezone(timezone.utc).isoformat()

        for ap in extract_airports(txt):
            key, name, icao = ap["key"], ap["name"], ap["icao"]

            # если раньше был slug, а сейчас пришёл код — слияние
            if icao and icao in hist and slug(name) in hist:
                merge_slug_into_icao(hist, slug(name), icao)

            rec = hist.setdefault(key, {"name": name, "icao": icao, "events": []})
            # если в старой записи не было кода, а теперь есть — обновляем
            if not rec.get("icao") and icao:
                rec["icao"] = icao
                # и возможно нужно сменить ключ
                if key != icao:
                    hist[icao] = rec
                    del hist[key]
                    key = icao
            rec["events"].append({"ts": ts, "status": status})
            rec["current"] = status

    save_hist(hist)
    build_site(hist)

if __name__ == "__main__":
    process()
