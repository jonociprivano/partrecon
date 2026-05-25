import re
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, jsonify, request

sys.path.insert(0, str(Path(__file__).parent))
from database.db import (get_listings_by_type, init_email_table, save_email, init_db)

app = Flask(__name__)

init_db()

CHASSIS_FILTERS = ["E46", "E90", "E92", "F80", "F82", "G80", "G82", "F87", "G87", "F90", "X3"]
REDDIT_SOURCES  = {"BimmerMarket", "E46", "E90", "F80", "BMWE46", "E9x"}

PART_TYPE_FILTERS = [
    "Wheels", "Exhaust", "Suspension", "Coilovers", "Brakes",
    "Engine", "Seats", "Exterior", "Interior", "Electronics",
]

_CHASSIS_RE = {
    code: re.compile(rf'\b{re.escape(code)}\b', re.IGNORECASE)
    for code in [
        "E46","E90","E92","E93",
        "F80","G80","F87","G87",
        "F90","G90","F10","X3","X4",
    ]
}
_PRICE_RE = re.compile(r'\$[\d,]+')
_SOLD_RE  = re.compile(r'\bsold\b|\[s\]|price\s+drop|reduced', re.IGNORECASE)

_PART_TYPE_MAP = {
    "wheels": "Wheels", "wheel": "Wheels", "wheels and tires": "Wheels",
    "tires": "Wheels", "tire": "Wheels", "rims": "Wheels",
    "wheel lock nuts": "Wheels", "wheel lock": "Wheels",
    "exhaust": "Exhaust", "exhaust tips": "Exhaust", "muffler": "Exhaust",
    "downpipe": "Exhaust", "catback": "Exhaust", "headers": "Exhaust",
    "catalyst": "Exhaust", "cats": "Exhaust",
    "coilovers": "Coilovers", "coilover": "Coilovers",
    "springs": "Suspension", "spring": "Suspension", "sway bar": "Suspension",
    "control arms": "Suspension", "control arm": "Suspension",
    "subframe": "Suspension", "suspension": "Suspension",
    "steering rack": "Suspension", "bushings": "Suspension",
    "subframe reinforcement": "Suspension",
    "front spindles/hubs/knuckles": "Suspension",
    "brakes": "Brakes", "brake": "Brakes", "brake calipers": "Brakes",
    "brake calipers and pads": "Brakes", "brake duct": "Brakes",
    "brake pads": "Brakes", "rotors": "Brakes",
    "engine": "Engine", "transmission": "Engine", "turbo": "Engine",
    "intake": "Engine", "air intake system": "Engine",
    "supercharger": "Engine", "oil cooler": "Engine",
    "cooling": "Engine", "intercooler": "Engine", "smg parts": "Engine",
    "seats": "Seats", "seat": "Seats",
    "interior": "Interior", "steering wheel": "Interior",
    "shift lever": "Interior", "dash": "Interior", "headliner": "Interior",
    "rear shade": "Interior", "rear sunshade": "Interior", "rear shades": "Interior",
    "bumper": "Exterior", "trunk lid": "Exterior", "hood": "Exterior",
    "splitter": "Exterior", "front splitter": "Exterior", "spoiler": "Exterior",
    "lip": "Exterior", "diffuser": "Exterior", "rocker panels": "Exterior",
    "fender": "Exterior", "door": "Exterior", "mirrors": "Exterior",
    "headlights": "Exterior", "taillights": "Exterior", "widebody": "Exterior",
    "head unit": "Electronics", "ecu": "Electronics",
    "electronics": "Electronics", "stereo": "Electronics", "speakers": "Electronics",
}


def normalize_part_type(raw: str) -> str:
    if not raw or raw.lower() in ("unknown", ""):
        return "Other"
    return _PART_TYPE_MAP.get(raw.strip().lower(), "Other")


def detect_chassis(title):
    return [c for c, p in _CHASSIS_RE.items() if p.search(title)]


_URL_CHASSIS_MAP = [
    ("e46m3.bimmerpost.com",  ["E46"]),
    ("f87.bimmerpost.com",    ["F87"]),
    ("g87.bimmerpost.com",    ["G87"]),
    ("f80.bimmerpost.com",    ["F80", "F82"]),
    ("g80.bimmerpost.com",    ["G80", "G82"]),
    ("f90.bimmerpost.com",    ["F90"]),
    ("f10.m5post.com",        ["F10"]),
    ("m3post.com",            ["E90", "E92"]),
    ("x3.xbimmers.com",       ["X3"]),
    ("/e46",                  ["E46"]),
    ("/e9x",                  ["E90", "E92"]),
]


def detect_chassis_from_url(url: str) -> list:
    url_lower = (url or "").lower()
    for pattern, codes in _URL_CHASSIS_MAP:
        if pattern in url_lower:
            return codes
    return []


def extract_price(title):
    m = _PRICE_RE.search(title)
    return m.group() if m else ""


def format_date(posted_at):
    if not posted_at:
        return ""
    try:
        dt = datetime.fromisoformat(posted_at[:19])
        return dt.strftime("%b") + " " + str(dt.day)
    except Exception:
        return ""


def badge_bg(source):
    if source in REDDIT_SOURCES:
        return "#8a6a4a"
    if source == "NAM3Forum":
        return "#5a7a5a"
    return "#4a6fa5"


def enrich(listing):
    title_chassis = detect_chassis(listing["title"])
    url_chassis   = detect_chassis_from_url(listing.get("url", ""))
    chassis = title_chassis + [c for c in url_chassis if c not in title_chassis]
    img = (listing.get("image_url") or "").strip()
    title = listing["title"]
    sold_m = _SOLD_RE.search(title)
    if sold_m:
        word = sold_m.group().upper()
        stamp = "PRICE DROP" if "price" in word.lower() or "reduced" in word.lower() else "SOLD"
    else:
        stamp = None
    display_title = (listing.get("normalized_title") or "").strip() or title
    return {
        **listing,
        "chassis_csv":      ",".join(chassis),
        "chassis_tags":     chassis,
        "price":            extract_price(title),
        "date_fmt":         format_date(listing.get("posted_at", "")),
        "badge_bg":         badge_bg(listing["source"]),
        "has_image":        (
            bool(img)
            and img not in ("self", "default")
            and img.startswith("https://")
            and "redd.it" not in img
            and "reddit.com" not in img
            and "redditstatic.com" not in img
        ),
        "sold_stamp":       stamp,
        "display_title":    display_title,
        "part_type_bucket": normalize_part_type(listing.get("part_type", "")),
    }


TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PartRecon — BMW Parts Aggregator</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-H4STZFJ3D0"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-H4STZFJ3D0');</script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #efefed; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.header { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 16px 56px; display: flex; justify-content: space-between; align-items: center; }
.header-left { display: flex; flex-direction: column; gap: 3px; }
.logo { display: flex; align-items: baseline; gap: 2px; text-decoration: none; }
.logo-part { font-size: 24px; font-weight: 700; font-style: italic; color: #111; }
.logo-dot { width: 5px; height: 5px; background: #2B4EFF; border-radius: 50%; margin: 0 2px 2px; flex-shrink: 0; align-self: center; }
.logo-recon { font-size: 24px; font-weight: 300; color: #888; letter-spacing: 3px; text-transform: uppercase; }
.tagline { font-size: 11px; color: #bbb; letter-spacing: 0.05em; text-transform: uppercase; padding-left: 2px; }
.nav { display: flex; gap: 28px; }
.nav a { font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: #999; text-decoration: none; }
.nav a.active { color: #2B4EFF; }
.search-bar { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 18px 56px 20px; }
.search-bar input { border: none; border-bottom: 1.5px solid #ddd; outline: none; font-size: 14px; width: 500px; background: transparent; color: #111; padding-bottom: 6px; }
.search-bar input::placeholder { color: #ccc; }
.filters { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 10px 56px; display: flex; flex-direction: column; gap: 8px; }
.filter-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.filter-label { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #bbb; min-width: 52px; flex-shrink: 0; }
.pill { border: 1px solid #ddd; border-radius: 20px; padding: 4px 13px; font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase; color: #999; background: #fff; cursor: pointer; }
.pill.active { border-color: #2B4EFF; color: #2B4EFF; background: #f0f2ff; }
.type-pill { border: 1px solid #ddd; border-radius: 20px; padding: 4px 13px; font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase; color: #999; background: #fff; cursor: pointer; }
.type-pill.active { border-color: #2B4EFF; color: #2B4EFF; background: #f0f2ff; }
.part-pill { border: 1px solid #ddd; border-radius: 20px; padding: 4px 13px; font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase; color: #999; background: #fff; cursor: pointer; }
.part-pill.active { border-color: #2B4EFF; color: #2B4EFF; background: #f0f2ff; }
.content { padding: 28px 56px; }
.tabs { display: flex; gap: 24px; border-bottom: 1px solid #e8e8e8; margin-bottom: 6px; }
.tab { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; padding-bottom: 10px; color: #bbb; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; background: none; border-left: none; border-right: none; border-top: none; }
.tab.active { color: #111; border-bottom: 2px solid #2B4EFF; font-weight: 500; }
.count { font-size: 11px; color: #bbb; letter-spacing: 0.05em; text-transform: uppercase; margin: 14px 0 10px; }
.controls-row { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-bottom: 16px; }
.price-range { display: flex; align-items: center; gap: 6px; }
.price-range input { border: 1px solid #e0e0e0; border-radius: 4px; padding: 5px 8px; font-size: 12px; width: 80px; outline: none; font-family: inherit; color: #111; background: #fff; -moz-appearance: textfield; }
.price-range input::-webkit-outer-spin-button, .price-range input::-webkit-inner-spin-button { -webkit-appearance: none; }
.price-range input:focus { border-color: #aaa; }
#sort-select { border: 1px solid #e0e0e0; border-radius: 4px; padding: 5px 10px; font-size: 12px; font-family: inherit; color: #666; cursor: pointer; outline: none; background: #fff; }
.grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.card { background: #fff; border: 1px solid #ebebeb; border-radius: 10px; overflow: hidden; cursor: pointer; display: flex; flex-direction: column; text-decoration: none; }
.card:hover .card-title { text-decoration: underline; }
.card-media { position: relative; flex-shrink: 0; }
.new-badge { position: absolute; top: 8px; left: 8px; background: #2B4EFF; color: #fff; font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; padding: 2px 7px; border-radius: 3px; z-index: 1; line-height: 1.5; }
.card-img { width: 100%; height: 140px; object-fit: cover; display: block; }
.card-placeholder { width: 100%; height: 140px; background: #1a1a1a; display: flex; align-items: center; justify-content: center; position: relative; overflow: hidden; }
.card-placeholder-grid { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: grid; grid-template-columns: repeat(6,1fr); grid-template-rows: repeat(4,1fr); }
.card-placeholder-grid div { border: 0.5px solid rgba(255,255,255,0.06); }
.card-placeholder-text { position: relative; font-size: 16px; font-weight: 700; font-style: italic; color: rgba(255,255,255,0.05); letter-spacing: -0.5px; }
.card-body { padding: 13px 14px 12px; flex: 1; display: flex; flex-direction: column; gap: 6px; }
.card-part-type { font-size: 10px; letter-spacing: 0.07em; text-transform: uppercase; color: #aaa; }
.card-title { font-size: 13px; font-weight: 500; color: #111; line-height: 1.45; flex: 1; }
.card-price { font-size: 14px; font-weight: 600; color: #111; letter-spacing: -0.3px; }
.card-footer { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #f2f2f2; padding-top: 9px; margin-top: 4px; }
.source-badge { font-size: 11px; color: #fff; background: #999; padding: 2px 8px; border-radius: 4px; }
.card-date { font-size: 11px; color: #bbb; }
.footer { background: #fff; border-top: 1px solid #e8e8e8; padding: 24px 56px; margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.footer p { font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: #bbb; }
.footer-disclaimer { font-size: 11px !important; letter-spacing: 0 !important; text-transform: none !important; color: #bbb; line-height: 1.5; }
.footer-links { font-size: 11px !important; letter-spacing: 0.06em !important; text-transform: uppercase !important; }
.footer-links a { color: #bbb; text-decoration: none; }
.footer-links a:hover { color: #888; }
.pagination { display: flex; align-items: center; gap: 20px; padding: 28px 0 8px; }
.pag-btn { background: none; border: none; cursor: pointer; font-family: inherit; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #999; padding: 0; }
.pag-btn:disabled { color: #ddd; cursor: default; }
.pag-btn:not(:disabled):hover { color: #111; }
.card.is-sold { opacity: 0.6; }
.sold-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.52); display: flex; align-items: center; justify-content: center; z-index: 2; }
.sold-stamp { color: #fff; font-size: 11px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; border: 2px solid rgba(255,255,255,0.65); padding: 5px 12px; border-radius: 3px; }
.email-banner { background: #fff; border-top: 1px solid #e8e8e8; padding: 24px 56px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px; }
.email-banner-left h3 { font-size: 14px; font-weight: 500; color: #111; margin-bottom: 4px; }
.email-banner-left p  { font-size: 12px; color: #999; line-height: 1.5; }
.email-banner-right   { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
#email-input { border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; font-size: 13px; width: 240px; outline: none; font-family: inherit; color: #111; }
#email-input:focus { border-color: #aaa; }
.email-submit-btn { background: #2B4EFF; color: #fff; border: none; border-radius: 6px; padding: 8px 16px; font-size: 13px; cursor: pointer; font-family: inherit; white-space: nowrap; }
.email-submit-btn:hover { background: #1a3de0; }
.email-success { font-size: 13px; color: #2B4EFF; font-weight: 500; }
.pag-info { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #2B4EFF; font-weight: 500; }
@media (max-width: 1023px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 599px) {
  .header { padding: 16px; flex-direction: column; align-items: center; gap: 10px; }
  .header-left { align-items: center; }
  .nav { justify-content: center; }
  .search-bar  { padding: 12px 16px 14px; }
  .filters     { padding: 10px 16px; }
  .content     { padding: 20px 16px; }
  .footer      { padding: 20px 16px; }
  .grid { grid-template-columns: 1fr; }
  .controls-row { flex-direction: column; align-items: stretch; gap: 8px; }
  .price-range  { width: 100%; }
  .price-range input { flex: 1; width: auto; min-width: 0; }
  #sort-select  { width: 100%; }
  .card-img        { height: 100px; }
  .card-placeholder { height: 100px; }
  .pagination { justify-content: center; }
}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <a href="/" class="logo">
      <span class="logo-part">Part</span>
      <div class="logo-dot"></div>
      <span class="logo-recon">Recon</span>
    </a>
    <span class="tagline">Aggregating BMW parts across the web</span>
  </div>
  <nav class="nav">
    <a href="#" class="active" data-type="part">Parts</a>
    <a href="#" data-type="vehicle">Vehicles</a>
    <a href="/about">About</a>
  </nav>
</div>

<div class="search-bar">
  <input id="search" type="search" placeholder="Search by title, part name, chassis code..." autocomplete="off" spellcheck="false" />
</div>

<div class="filters">
  <div class="filter-row">
    <span class="filter-label">Chassis</span>
    <button class="pill active" data-chassis="">All</button>
    {% for code in chassis_filters %}
    <button class="pill" data-chassis="{{ code }}">{{ code }}</button>
    {% endfor %}
  </div>
  <div class="filter-row" id="part-type-row">
    <span class="filter-label">Part</span>
    <button class="part-pill active" data-part-type="">All</button>
    {% for pt in part_type_filters %}
    <button class="part-pill" data-part-type="{{ pt }}">{{ pt }}</button>
    {% endfor %}
  </div>
  <div class="filter-row">
    <span class="filter-label">Type</span>
    <button class="type-pill active" data-post-type="">All Types</button>
    <button class="type-pill" data-post-type="FS">For Sale</button>
    <button class="type-pill" data-post-type="WTB">WTB</button>
  </div>
</div>

<div class="content">

  <div class="tabs">
    <button class="tab active" data-type="part">Parts</button>
    <button class="tab" data-type="vehicle">Vehicles</button>
  </div>

  <div class="count" id="count"></div>

  <div class="controls-row">
    <div class="price-range">
      <input type="number" id="min-price" placeholder="Min $" min="0" />
      <input type="number" id="max-price" placeholder="Max $" min="0" />
    </div>
    <select id="sort-select">
      <option value="newest">Newest First</option>
      <option value="price-asc">Price: Low to High</option>
      <option value="price-desc">Price: High to Low</option>
    </select>
  </div>

  <div class="grid" id="grid">
    {% for listing in listings %}
    <a class="card{% if listing.sold_stamp %} is-sold{% endif %}"
       href="{{ listing.url }}"
       target="_blank"
       rel="noopener noreferrer"
       data-type="{{ listing.listing_type | e }}"
       data-chassis="{{ listing.chassis_csv | e }}"
       data-title="{{ listing.display_title | lower | e }}"
       data-price="{{ listing.price if listing.price is not none else '' }}"
       data-post-type="{{ (listing.post_type or 'UNKNOWN') | e }}"
       data-part-type="{{ listing.part_type_bucket | e }}">

      <div class="card-media">
        {% if listing.is_new %}<span class="new-badge">New</span>{% endif %}
        {% if listing.has_image %}
          <img class="card-img" src="{{ listing.image_url }}" alt="" loading="lazy" />
        {% else %}
          <div class="card-placeholder">
            <div class="card-placeholder-grid">
              {% for _ in range(24) %}<div></div>{% endfor %}
            </div>
            <span class="card-placeholder-text">Part&middot;Recon</span>
          </div>
        {% endif %}
        {% if listing.sold_stamp %}
        <div class="sold-overlay"><span class="sold-stamp">{{ listing.sold_stamp }}</span></div>
        {% endif %}
      </div>

      <div class="card-body">
        {% if listing.part_type_bucket and listing.part_type_bucket != 'Other' %}
        <div class="card-part-type">{{ listing.part_type_bucket }}</div>
        {% endif %}
        <div class="card-title">{{ listing.display_title }}</div>
        {% if listing.price %}
        <div class="card-price">{{ listing.price }}</div>
        {% endif %}
        <div class="card-footer">
          <span class="source-badge" style="background:{{ listing.badge_bg }}">{{ listing.source }}</span>
          <span class="card-date">{{ listing.date_fmt }}</span>
        </div>
      </div>

    </a>
    {% endfor %}
  </div>

  <div class="pagination">
    <button class="pag-btn" id="prev-btn">&#8592; Prev</button>
    <span class="pag-info" id="page-info">Page 1 of 1</span>
    <button class="pag-btn" id="next-btn">Next &#8594;</button>
  </div>

</div>

<div class="email-banner" id="email-banner">
  <div class="email-banner-left">
    <h3>Get notified when search alerts go live</h3>
    <p>Be first to know when we launch saved searches and instant notifications.</p>
  </div>
  <div class="email-banner-right" id="email-form-wrap">
    <input type="email" id="email-input" placeholder="your@email.com" />
    <button class="email-submit-btn" onclick="submitEmail()">Submit</button>
  </div>
</div>

<div class="footer">
  <p>PartRecon &mdash; Aggregating BMW parts across the web</p>
  <p class="footer-disclaimer">PartRecon aggregates publicly available listings from BMW forums. We do not own or verify any listing content.</p>
  <p class="footer-links"><a href="/terms">Terms</a> &middot; <a href="/privacy">Privacy</a></p>
</div>

<script>
(function () {
  'use strict';

  var PAGE_SIZE  = 40;
  var currentPage = 1;
  var lastSorted  = [];

  var cards      = Array.from(document.querySelectorAll('.card'));
  var search     = document.getElementById('search');
  var pills      = Array.from(document.querySelectorAll('.pill'));
  var typePills  = Array.from(document.querySelectorAll('.type-pill'));
  var partPills  = Array.from(document.querySelectorAll('.part-pill'));
  var tabs       = Array.from(document.querySelectorAll('.tab'));
  var navLinks   = Array.from(document.querySelectorAll('.nav a[data-type]'));
  var countEl    = document.getElementById('count');
  var prevBtn    = document.getElementById('prev-btn');
  var nextBtn    = document.getElementById('next-btn');
  var pageInfo   = document.getElementById('page-info');
  var sortSel    = document.getElementById('sort-select');
  var minEl      = document.getElementById('min-price');
  var maxEl      = document.getElementById('max-price');
  var partRow    = document.getElementById('part-type-row');

  var state = { type: 'part', chassis: '', term: '', postType: '', partType: '', sort: 'newest' };

  function price(c) {
    var v = c.dataset.price;
    return (v && v !== '') ? parseFloat(v) : null;
  }

  function syncPartRow() {
    if (partRow) partRow.style.display = state.type === 'vehicle' ? 'none' : '';
  }

  function run() {
    var minP = (minEl && minEl.value !== '') ? parseFloat(minEl.value) : null;
    var maxP = (maxEl && maxEl.value !== '') ? parseFloat(maxEl.value) : null;

    var filtered = cards.filter(function (c) {
      if (c.dataset.type !== state.type) return false;
      if (state.chassis) {
        var ch = (c.dataset.chassis || '').split(',');
        if (ch.indexOf(state.chassis) === -1) return false;
      }
      if (state.postType && (c.dataset.postType || '') !== state.postType) return false;
      if (state.partType && (c.dataset.partType || '') !== state.partType) return false;
      if (state.term && (c.dataset.title || '').indexOf(state.term) === -1) return false;
      if (minP !== null || maxP !== null) {
        var p = price(c);
        if (p === null) return false;
        if (minP !== null && p < minP) return false;
        if (maxP !== null && p > maxP) return false;
      }
      return true;
    });

    var sorted = filtered.slice();
    if (state.sort === 'price-asc') {
      sorted.sort(function (a, b) {
        var pa = price(a), pb = price(b);
        if (pa === null) return  1;
        if (pb === null) return -1;
        return pa - pb;
      });
    } else if (state.sort === 'price-desc') {
      sorted.sort(function (a, b) {
        var pa = price(a), pb = price(b);
        if (pa === null) return  1;
        if (pb === null) return -1;
        return pb - pa;
      });
    }
    cards.forEach(function (c) { c.style.order = ''; });
    sorted.forEach(function (c, i) { c.style.order = i; });

    lastSorted = sorted;
    var total      = sorted.length;
    var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1)          currentPage = 1;

    var start     = (currentPage - 1) * PAGE_SIZE;
    var pageSlice = sorted.slice(start, start + PAGE_SIZE);

    cards.forEach(function (c) { c.style.display = 'none'; });
    pageSlice.forEach(function (c) { c.style.display = ''; });

    if (countEl)  countEl.textContent  = total.toLocaleString() + ' LISTINGS';
    if (pageInfo) pageInfo.textContent = 'Page ' + currentPage + ' of ' + totalPages;
    if (prevBtn)  prevBtn.disabled     = (currentPage <= 1);
    if (nextBtn)  nextBtn.disabled     = (currentPage >= totalPages);
  }

  function setType(type) {
    state.type  = type;
    currentPage = 1;
    tabs.forEach(function (t) { t.classList.toggle('active', t.dataset.type === type); });
    navLinks.forEach(function (a) { a.classList.toggle('active', a.dataset.type === type); });
    syncPartRow();
    run();
  }

  tabs.forEach(function (t) {
    t.addEventListener('click', function () { setType(t.dataset.type); });
  });
  navLinks.forEach(function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); setType(a.dataset.type); });
  });
  pills.forEach(function (p) {
    p.addEventListener('click', function () {
      pills.forEach(function (x) { x.classList.remove('active'); });
      p.classList.add('active');
      state.chassis = p.dataset.chassis || '';
      currentPage = 1; run();
    });
  });
  typePills.forEach(function (p) {
    p.addEventListener('click', function () {
      typePills.forEach(function (x) { x.classList.remove('active'); });
      p.classList.add('active');
      state.postType = p.dataset.postType || '';
      currentPage = 1; run();
    });
  });
  partPills.forEach(function (p) {
    p.addEventListener('click', function () {
      partPills.forEach(function (x) { x.classList.remove('active'); });
      p.classList.add('active');
      state.partType = p.dataset.partType || '';
      currentPage = 1; run();
    });
  });
  if (prevBtn) prevBtn.addEventListener('click', function () {
    if (currentPage > 1) { currentPage--; run(); window.scrollTo(0, 0); }
  });
  if (nextBtn) nextBtn.addEventListener('click', function () {
    var tp = Math.max(1, Math.ceil(lastSorted.length / PAGE_SIZE));
    if (currentPage < tp) { currentPage++; run(); window.scrollTo(0, 0); }
  });
  if (search) search.addEventListener('input', function () {
    state.term = search.value.toLowerCase().trim();
    currentPage = 1; run();
  });
  if (sortSel) sortSel.addEventListener('change', function () {
    state.sort = sortSel.value; currentPage = 1; run();
  });
  function onPrice() { currentPage = 1; run(); }
  if (minEl) minEl.addEventListener('input', onPrice);
  if (maxEl) maxEl.addEventListener('input', onPrice);

  syncPartRow();
  run();
}());

function submitEmail() {
  var input = document.getElementById('email-input');
  var wrap  = document.getElementById('email-form-wrap');
  var email = input ? input.value.trim() : '';
  if (!email || !/^[^@]+@[^@]+\\.[^@]+$/.test(email)) {
    input.style.borderColor = '#e33';
    return;
  }
  fetch('/subscribe', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: email})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      wrap.innerHTML = '<span class="email-success">Thanks&#33; We&#39;ll be in touch.</span>';
    }
  });
}
</script>
</body>
</html>
"""


_SHELL_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:#efefed;-webkit-font-smoothing:antialiased}
.header{background:#fff;border-bottom:1px solid #e8e8e8;padding:16px 56px;display:flex;justify-content:space-between;align-items:center}
.header-left{display:flex;flex-direction:column;gap:3px}
.logo{display:flex;align-items:baseline;gap:2px;text-decoration:none}
.logo-part{font-size:24px;font-weight:700;font-style:italic;color:#111}
.logo-dot{width:5px;height:5px;background:#2B4EFF;border-radius:50%;margin:0 2px 2px;flex-shrink:0;align-self:center}
.logo-recon{font-size:24px;font-weight:300;color:#888;letter-spacing:3px;text-transform:uppercase}
.tagline{font-size:11px;color:#bbb;letter-spacing:0.05em;text-transform:uppercase;padding-left:2px}
.nav{display:flex;gap:28px}
.nav a{font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#999;text-decoration:none}
.nav a.active{color:#2B4EFF}
.email-banner{background:#fff;border-top:1px solid #e8e8e8;padding:24px 56px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px}
.email-banner-left h3{font-size:14px;font-weight:500;color:#111;margin-bottom:4px}
.email-banner-left p{font-size:12px;color:#999;line-height:1.5}
.email-banner-right{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#email-input{border:1px solid #ddd;border-radius:6px;padding:8px 12px;font-size:13px;width:240px;outline:none;font-family:inherit;color:#111}
.email-submit-btn{background:#2B4EFF;color:#fff;border:none;border-radius:6px;padding:8px 16px;font-size:13px;cursor:pointer;font-family:inherit}
.email-success{font-size:13px;color:#2B4EFF;font-weight:500}
.site-footer{background:#fff;border-top:1px solid #e8e8e8;padding:24px 56px;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#bbb;display:flex;flex-direction:column;gap:6px}
.site-footer-disclaimer{font-size:11px;letter-spacing:0;text-transform:none;color:#bbb;line-height:1.5}
.site-footer-links{font-size:11px;letter-spacing:0.06em}
.site-footer-links a{color:#bbb;text-decoration:none}
.site-footer-links a:hover{color:#888}
@media(max-width:599px){.header{padding:16px;flex-direction:column;align-items:center;gap:10px}.header-left{align-items:center}.nav{justify-content:center}.email-banner,.site-footer{padding-left:16px;padding-right:16px}}
"""

_EMAIL_BANNER = """
<div class="email-banner">
  <div class="email-banner-left">
    <h3>Get notified when search alerts go live</h3>
    <p>Be first to know when we launch saved searches and instant notifications.</p>
  </div>
  <div class="email-banner-right" id="email-form-wrap">
    <input type="email" id="email-input" placeholder="your@email.com" />
    <button class="email-submit-btn" onclick="submitEmail()">Submit</button>
  </div>
</div>
"""

_EMAIL_JS = """
<script>
function submitEmail() {
  var input = document.getElementById('email-input');
  var wrap  = document.getElementById('email-form-wrap');
  var email = input ? input.value.trim() : '';
  if (!email || !/^[^@]+@[^@]+\\.[^@]+$/.test(email)) { input.style.borderColor='#e33'; return; }
  fetch('/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})})
  .then(function(r){return r.json();}).then(function(d){if(d.ok)wrap.innerHTML='<span class="email-success">Thanks! We\\'ll be in touch.</span>';});
}
</script>
"""

_SHELL_HEADER = """
<div class="header">
  <div class="header-left">
    <a href="/" class="logo">
      <span class="logo-part">Part</span>
      <div class="logo-dot"></div>
      <span class="logo-recon">Recon</span>
    </a>
    <span class="tagline">Aggregating BMW parts across the web</span>
  </div>
  <nav class="nav">
    <a href="/">Parts</a>
    <a href="/">Vehicles</a>
    <a href="/about"{about_active}>About</a>
  </nav>
</div>
"""

_SHELL_FOOTER = """
{email_banner}
<div class="site-footer">
  <div>PartRecon &mdash; Aggregating BMW parts across the web</div>
  <div class="site-footer-disclaimer">PartRecon aggregates publicly available listings from BMW forums. We do not own or verify any listing content.</div>
  <div class="site-footer-links"><a href="/terms">Terms</a> &middot; <a href="/privacy">Privacy</a></div>
</div>
{email_js}
"""

ABOUT_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PartRecon — About</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-H4STZFJ3D0"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-H4STZFJ3D0');</script>
<style>
{css}
.about-wrap{{max-width:680px;margin:56px auto;padding:0 56px}}
.about-wrap h1{{font-size:26px;font-weight:700;color:#111;margin-bottom:28px;letter-spacing:-0.4px}}
.about-wrap p{{font-size:15px;color:#444;line-height:1.75;margin-bottom:20px}}
.about-wrap a{{color:#2B4EFF;text-decoration:none}}
.about-wrap a:hover{{text-decoration:underline}}
@media(max-width:599px){{.about-wrap{{padding:0 16px;margin:36px auto}}}}
</style>
</head>
<body>
{header}
<div class="about-wrap">
  <h1>About PartRecon</h1>
  <p>PartRecon aggregates BMW parts listings from across the web into one searchable place. Instead of checking Bimmerpost, NAM3Forum, Reddit, and dozens of other forums manually, PartRecon pulls every new listing automatically and displays them in one clean feed.</p>
  <p>We currently pull from Bimmerpost (M2, M3, M4, M5, X3), NAM3Forum (E46 M3, E9x), and BMW subreddits. New sources are added regularly. Listings are refreshed every 15 minutes.</p>
  <p>Built by a BMW enthusiast, for BMW enthusiasts. Have feedback or want to suggest a source? Email <a href="mailto:partrecon@gmail.com">partrecon@gmail.com</a></p>
</div>
{footer}
</body>
</html>
""".format(
    css=_SHELL_CSS,
    header=_SHELL_HEADER.format(about_active=' class="active"'),
    footer=_SHELL_FOOTER.format(email_banner=_EMAIL_BANNER, email_js=_EMAIL_JS),
)

_LEGAL_CSS = _SHELL_CSS + """
.legal-wrap{{max-width:680px;margin:56px auto;padding:0 56px}}
.legal-wrap h1{{font-size:26px;font-weight:700;color:#111;margin-bottom:28px;letter-spacing:-0.4px}}
.legal-wrap h2{{font-size:15px;font-weight:600;color:#111;margin:28px 0 10px}}
.legal-wrap p{{font-size:15px;color:#444;line-height:1.75;margin-bottom:14px}}
.legal-wrap a{{color:#2B4EFF;text-decoration:none}}
.legal-wrap a:hover{{text-decoration:underline}}
@media(max-width:599px){{.legal-wrap{{padding:0 16px;margin:36px auto}}}}
"""

TERMS_TEMPLATE = (
    "<!DOCTYPE html><html><head>"
    "<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Terms of Service — PartRecon</title>"
    "<style>" + _LEGAL_CSS + "</style></head><body>"
    + _SHELL_HEADER.format(about_active="") +
    """
<div class="legal-wrap">
  <h1>Terms of Service</h1>
  <h2>1. Content Ownership</h2>
  <p>PartRecon aggregates publicly available listings from BMW forums and communities across the internet. We do not own, create, or verify any listing content. All listings remain the property of their original authors and platforms.</p>
  <h2>2. No Warranty on Listings</h2>
  <p>Listings displayed on PartRecon may be outdated, inaccurate, or no longer available. We make no representations about the accuracy, completeness, or availability of any listing. Always verify directly with the original source before making any purchase decisions.</p>
  <h2>3. Transactions</h2>
  <p>PartRecon is an aggregation service only. We are not a party to any transaction between buyers and sellers. We accept no responsibility or liability for any transaction, dispute, loss, or damage arising from listings found through this site.</p>
  <h2>4. Age Requirement</h2>
  <p>You must be 18 years of age or older to use PartRecon.</p>
  <h2>5. Content Removal</h2>
  <p>We reserve the right to remove any listing or content at any time for any reason, including but not limited to content that is fraudulent, illegal, or violates these terms.</p>
  <h2>6. Changes to Terms</h2>
  <p>We may update these terms at any time. Continued use of PartRecon after changes constitutes acceptance of the updated terms.</p>
  <h2>Contact</h2>
  <p>Questions about these terms? Email <a href="mailto:partrecon@gmail.com">partrecon@gmail.com</a></p>
</div>
"""
    + _SHELL_FOOTER.format(email_banner=_EMAIL_BANNER, email_js=_EMAIL_JS)
    + "</body></html>"
)

PRIVACY_TEMPLATE = (
    "<!DOCTYPE html><html><head>"
    "<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Privacy Policy — PartRecon</title>"
    "<style>" + _LEGAL_CSS + "</style></head><body>"
    + _SHELL_HEADER.format(about_active="") +
    """
<div class="legal-wrap">
  <h1>Privacy Policy</h1>
  <h2>Information We Collect</h2>
  <p><strong>Email addresses:</strong> If you voluntarily submit your email address through our notification signup form, we store it securely. We use it only to notify you about PartRecon features and updates.</p>
  <p><strong>Analytics data:</strong> We use Google Analytics 4 to understand how visitors use the site. This includes pages visited, time on site, and general location (country/region). Google Analytics uses cookies to collect this data. No personally identifiable information is collected through analytics.</p>
  <h2>How We Use Your Information</h2>
  <p>Email addresses are used solely to send PartRecon product updates. We do not send marketing emails on behalf of third parties. Analytics data is used to improve the site experience.</p>
  <h2>Data Sharing</h2>
  <p>We do not sell, rent, or share your personal information with third parties. Analytics data is processed by Google under their privacy policy.</p>
  <h2>Cookies</h2>
  <p>PartRecon uses cookies through Google Analytics to measure site usage. These cookies do not identify you personally. You can disable cookies in your browser settings, though this may affect site functionality.</p>
  <h2>Data Deletion</h2>
  <p>To request deletion of your email address or any other data we hold about you, email <a href="mailto:partrecon@gmail.com">partrecon@gmail.com</a>. We will process your request within 30 days.</p>
  <h2>Changes to This Policy</h2>
  <p>We may update this policy at any time. The current version is always available at this URL.</p>
  <h2>Contact</h2>
  <p>Privacy questions? Email <a href="mailto:partrecon@gmail.com">partrecon@gmail.com</a></p>
</div>
"""
    + _SHELL_FOOTER.format(email_banner=_EMAIL_BANNER, email_js=_EMAIL_JS)
    + "</body></html>"
)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    parts    = [enrich(l) for l in get_listings_by_type("part")]
    vehicles = [enrich(l) for l in get_listings_by_type("vehicle")]
    return render_template_string(
        TEMPLATE,
        listings=parts + vehicles,
        chassis_filters=CHASSIS_FILTERS,
        part_type_filters=PART_TYPE_FILTERS,
        parts_count=len(parts),
        vehicles_count=len(vehicles),
    )


@app.route("/about")
def about():
    return ABOUT_TEMPLATE


@app.route("/terms")
def terms():
    return TERMS_TEMPLATE


@app.route("/privacy")
def privacy():
    return PRIVACY_TEMPLATE


@app.route("/subscribe", methods=["POST"])
def subscribe():
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return jsonify({"ok": False, "error": "Invalid email"}), 400
    init_email_table()
    save_email(email)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5001)