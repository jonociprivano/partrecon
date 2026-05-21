"""
scraper/parser.py — Claude API parser for listing titles.

Takes a raw forum listing title and returns structured fields:
  - part_type:        normalized part name  e.g. "Trunk Lid", "Coilovers", "Wheels"
  - condition:        "New", "Used", "Unknown"
  - normalized_title: clean, readable version stripped of forum noise

Called at insert time for every new listing. Costs ~$0.00003 per call.
"""

import os
import json
import logging
import anthropic

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


_SYSTEM = """\
You are a BMW parts listing parser. Given a raw forum classified ad title, extract structured fields.

Respond ONLY with a valid JSON object — no preamble, no markdown, no explanation.

Fields:
- part_type (string): The specific part or item being sold/wanted. Normalize to clean English.
  Examples: "Trunk Lid", "Coilovers", "Wheels and Tires", "Bumper", "ECU", "Exhaust",
  "Control Arms", "Seats", "Full Car", "Engine", "Transmission", "Headlights"
  If you can't determine a specific part, return "Unknown"
- condition (string): One of "New", "Used", "Unknown"
  "New" if title says OEM new, brand new, NIB, NOS, never installed, or similar
  "Used" if title implies a used/pulled part
  "Unknown" if not clear
- normalized_title (string): A clean, readable version of the title.
  Remove: [FS], (WTB), pricing noise, excessive punctuation, forum prefixes
  Keep: part name, chassis code, key descriptors (color, brand, condition)
  Max 80 characters.

Examples:
Input:  "FS: OEM E46 M3 CSL trunk lid — $1200 shipped, trades welcome!!"
Output: {"part_type": "Trunk Lid", "condition": "Used", "normalized_title": "E46 M3 CSL Trunk Lid"}

Input:  "[WTB] F80 M3 competition seats, prefer black, will pay shipping"
Output: {"part_type": "Seats", "condition": "Unknown", "normalized_title": "F80 M3 Competition Seats (WTB)"}

Input:  "Brand New Bilstein B16 coilovers E90 E92 — never installed $850"
Output: {"part_type": "Coilovers", "condition": "New", "normalized_title": "Bilstein B16 Coilovers E90/E92 — New"}

Input:  "WestForged 19\" For sale or trade"
Output: {"part_type": "Wheels", "condition": "Used", "normalized_title": "WestForged 19\" Wheels"}
"""


def parse_listing(title: str) -> dict:
    """
    Parse a listing title using Claude.
    Returns dict with part_type, condition, normalized_title.
    Falls back to safe defaults on any error so scraping never breaks.
    """
    try:
        client = _get_client()
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",   # cheapest + fast, perfect for this
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": title}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown fences if model adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        return {
            "part_type":        str(result.get("part_type", "Unknown"))[:100],
            "condition":        str(result.get("condition", "Unknown"))[:20],
            "normalized_title": str(result.get("normalized_title", title))[:200],
        }
    except Exception as e:
        logger.warning(f"Parser failed for title '{title[:60]}': {e}")
        return {
            "part_type":        "Unknown",
            "condition":        "Unknown",
            "normalized_title": title[:200],
        }
