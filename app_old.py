import re
from flask import Flask, render_template_string
from database.db import get_listings_by_type

app = Flask(__name__)

REDDIT_SOURCES  = {"BimmerMarket", "E46", "E90", "F80", "BMWE46", "E9x"}
CHASSIS_FILTERS = ["E46", "E90", "E92", "F80", "G80", "F87", "F90", "X3"]

_CHASSIS_RE = {
    code: re.compile(rf'\b{re.escape(code)}\b', re.IGNORECASE)
    for code in [
        "E46","E90","E92","E93",
        "F80","G80","F87","G87",
        "F90","G90","F10","X3","X4",
    ]
}
_PRICE_RE = re.compile(r'\$[\d,]+(?:\.\d{2})?')


def detect_chassis(title):
    return [c for c, p in _CHASSIS_RE.items() if p.search(title)]

def extract_price(title):
    m = _PRICE_RE.search(title)
    return m.group() if m else ""

def enrich(listing):
    src = listing["source"]
    source_display = "Reddit" if src in REDDIT_SOURCES else src
    chassis = detect_chassis(listing["title"])
    return {
        **listing,
        "source_display": source_display,
        "chassis_tags":   chassis,
        "chassis_csv":    ",".join(chassis),
        "price":          extract_price(listing["title"]),
    }


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>PartRecon</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
         background:#efefed;-webkit-font-smoothing:antialiased}
    input::placeholder{color:#ccc}
    .pill{border:1px solid #ddd;border-radius:20px;padding:4px 13px;font-size:11px;
          letter-spacing:0.07em;text-transform:uppercase;color:#999;background:#fff;
          cursor:pointer;font-family:inherit}
    .pill.active{border-color:#2B4EFF;color:#2B4EFF;background:#f0f2ff}
  </style>
</head>
<body>

<header style="background:#fff;border-bottom:1px solid #e8e8e8;padding:20px 56px;display:flex;justify-content:space-between;align-items:center">

  <div style="display:flex;align-items:baseline;gap:2px">
    <span style="font-size:24px;font-weight:700;font-style:italic;color:#111">Part</span>
    <div style="width:6px;height:6px;background:#2B4EFF;border-radius:50%;margin:0 3px 4px"></div>
    <span style="font-size:24px;font-weight:300;color:#888;letter-spacing:3px;text-transform:uppercase">Recon</span>
  </div>

  <nav style="display:flex;gap:28px;align-items:center">
    <button style="background:none;border:none;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:#2B4EFF;cursor:pointer;padding:0;font-family:inherit">Parts</button>
    <button style="background:none;border:none;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:#999;cursor:pointer;padding:0;font-family:inherit">Vehicles</button>
    <button style="background:none;border:none;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:#999;cursor:pointer;padding:0;font-family:inherit">About</button>
  </nav>

</header>

<div style="background:#fff;border-bottom:1px solid #e8e8e8;padding:18px 56px 20px">
  <input id="search" type="search" placeholder="Search listings…"
         style="border:none;border-bottom:1.5px solid #ddd;outline:none;font-size:14px;width:500px;background:transparent;color:#111;font-family:inherit;padding-bottom:8px"/>
</div>

<div style="background:#fff;border-bottom:1px solid #e8e8e8;padding:12px 56px">
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="pill active">All</button>
    {% for code in chassis_filters %}
    <button class="pill">{{ code }}</button>
    {% endfor %}
  </div>
</div>

<main></main>

</body>
</html>"""


@app.route("/")
def index():
    parts    = [enrich(l) for l in get_listings_by_type("part")]
    vehicles = [enrich(l) for l in get_listings_by_type("vehicle")]
    return render_template_string(
        TEMPLATE,
        chassis_filters=CHASSIS_FILTERS,
        parts_count=len(parts),
        vehicles_count=len(vehicles),
        listings=parts + vehicles,
    )


if __name__ == "__main__":
    app.run(debug=True)
