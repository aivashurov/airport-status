#!/usr/bin/env python3
import feedparser, re, json, html
from datetime import timezone
from dateutil import parser as dparse
from pathlib import Path
from jinja2 import Template

# RSS‑Bridge выдаёт Atom‑фид Telegram‑канала @korenyako
FEED_URL = ("https://wtf.roflcopter.fr/rss-bridge/?action=display"
            "&bridge=Telegram&username=korenyako&format=Atom")

# Регулярки
RE_NEW  = re.compile(r"(временн(?:ые|ых) ограничения).*?введен", re.I | re.S)
RE_LIFT = re.compile(r"(ограничени[яе].*?снят|возобновил[аи])", re.I | re.S)
RE_AP   = re.compile(r"(?P<name>[А-ЯЁA-Za-z\-\s]+?)\s*\([^)]*(?P<icao>[A-Z]{4})\)", re.U)

def load_hist():
    return json.loads(Path("status.json").read_text()) if Path("status.json").exists() else {}

def save_hist(h): Path("status.json").write_text(json.dumps(h, ensure_ascii=False, indent=2))

def classify(t):  # closed / open / None
    if RE_NEW.search(t):   return "closed"
    if RE_LIFT.search(t):  return "open"

def airports(t):  # [{'name':'Внуково','icao':'UUWW'}, …]
    return [m.groupdict() for m in RE_AP.finditer(t)]

def build_site(hist):
    for tpl, out in [("index.html", "index.html"), ("history.html", "history.html")]:
        tmpl = Template(Path(f"templates/{tpl}").read_text())
        Path(out).write_text(tmpl.render(airports=hist))

def process():
    hist = load_hist()
    for e in sorted(feedparser.parse(FEED_URL)["entries"], key=lambda x: x["published_parsed"]):
        txt = html.unescape(e["title"] + "\n" + e.get("summary", ""))
        status = classify(txt)
        if not status:
            continue
        ts = dparse.parse(e["published"]).astimezone(timezone.utc).isoformat()
        for ap in airports(txt):
            icao = ap["icao"]
            hist.setdefault(icao, {"name": ap["name"].strip(), "events": []})
            hist[icao]["events"].append({"ts": ts, "status": status})
            hist[icao]["current"] = status
    save_hist(hist)
    build_site(hist)

if __name__ == "__main__":
    process()
