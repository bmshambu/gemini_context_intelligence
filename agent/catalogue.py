"""Mock product catalogue + preference-based recommendations + currency pricing.

Prices are stored in a base currency (USD) and converted to the shopper's currency
(from their country preference) for display. Recommendations filter by gender and
age group, and are boosted/highlighted by interest overlap.
"""
from __future__ import annotations

# id, name, emoji, category, gender (any|men|women), age_group (any|kid|teen|adult|senior),
# interests (tags), price_usd
PRODUCTS = [
    {"id": "p01", "name": "Wireless Noise-Cancelling Headphones", "emoji": "🎧", "category": "Electronics",
     "gender": "any", "age_group": "adult", "interests": ["music", "tech", "travel"], "price_usd": 199},
    {"id": "p02", "name": "Running Shoes (Lightweight)", "emoji": "👟", "category": "Footwear",
     "gender": "any", "age_group": "adult", "interests": ["fitness", "running", "sports"], "price_usd": 89},
    {"id": "p03", "name": "Yoga Mat (Eco Cork)", "emoji": "🧘", "category": "Fitness",
     "gender": "any", "age_group": "adult", "interests": ["fitness", "yoga", "wellness"], "price_usd": 39},
    {"id": "p04", "name": "Building Blocks Set (200 pcs)", "emoji": "🧱", "category": "Toys",
     "gender": "any", "age_group": "kid", "interests": ["toys", "learning", "creativity"], "price_usd": 29},
    {"id": "p05", "name": "Graphic Novel Box Set", "emoji": "📚", "category": "Books",
     "gender": "any", "age_group": "teen", "interests": ["reading", "comics", "art"], "price_usd": 45},
    {"id": "p06", "name": "Skincare Gift Set", "emoji": "🧴", "category": "Beauty",
     "gender": "women", "age_group": "adult", "interests": ["beauty", "self-care", "wellness"], "price_usd": 55},
    {"id": "p07", "name": "Leather Wallet (RFID)", "emoji": "👛", "category": "Accessories",
     "gender": "men", "age_group": "adult", "interests": ["fashion", "travel"], "price_usd": 49},
    {"id": "p08", "name": "Smartwatch (Fitness+)", "emoji": "⌚", "category": "Electronics",
     "gender": "any", "age_group": "adult", "interests": ["fitness", "tech", "health"], "price_usd": 149},
    {"id": "p09", "name": "Cast-Iron Cookware Set", "emoji": "🍳", "category": "Home & Kitchen",
     "gender": "any", "age_group": "adult", "interests": ["cooking", "home", "food"], "price_usd": 120},
    {"id": "p10", "name": "Acoustic Guitar (Beginner)", "emoji": "🎸", "category": "Music",
     "gender": "any", "age_group": "teen", "interests": ["music", "hobby", "art"], "price_usd": 110},
    {"id": "p11", "name": "Ergonomic Reading Glasses", "emoji": "👓", "category": "Accessories",
     "gender": "any", "age_group": "senior", "interests": ["reading", "comfort", "health"], "price_usd": 35},
    {"id": "p12", "name": "Board Game Night Bundle", "emoji": "🎲", "category": "Games",
     "gender": "any", "age_group": "any", "interests": ["games", "family", "fun"], "price_usd": 59},
    # ── Entertainment ────────────────────────────────────────────────────────
    {"id": "p13", "name": "4K Streaming Media Player", "emoji": "🎬", "category": "Entertainment",
     "gender": "any", "age_group": "adult", "interests": ["entertainment", "movies", "tech", "streaming"], "price_usd": 49},
    {"id": "p14", "name": "Bluetooth Party Speaker", "emoji": "🔊", "category": "Entertainment",
     "gender": "any", "age_group": "teen", "interests": ["entertainment", "music", "party"], "price_usd": 79},
    {"id": "p15", "name": "Handheld Gaming Console", "emoji": "🎮", "category": "Entertainment",
     "gender": "any", "age_group": "teen", "interests": ["entertainment", "gaming", "tech"], "price_usd": 199},
    {"id": "p16", "name": "Portable Movie Projector", "emoji": "📽️", "category": "Entertainment",
     "gender": "any", "age_group": "adult", "interests": ["entertainment", "movies", "home"], "price_usd": 130},
    {"id": "p17", "name": "VR Headset (Immersive)", "emoji": "🥽", "category": "Entertainment",
     "gender": "any", "age_group": "teen", "interests": ["entertainment", "gaming", "tech", "vr"], "price_usd": 299},
    {"id": "p18", "name": "Karaoke Microphone Set", "emoji": "🎤", "category": "Entertainment",
     "gender": "any", "age_group": "any", "interests": ["entertainment", "music", "party", "fun"], "price_usd": 45},
]


# Profiles say male/female; the catalogue tags products men/women. Normalize so
# they match (else a male shopper never sees men's items, etc.).
_GENDER_ALIASES = {"male": "men", "man": "men", "men": "men",
                   "female": "women", "woman": "women", "women": "women"}


def _norm_gender(g: str) -> str:
    return _GENDER_ALIASES.get((g or "").strip().lower(), "")


def _matches(product: dict, gender: str, age_group: str) -> bool:
    g = _norm_gender(gender)      # "" when unknown / "any" → no gender restriction
    a = age_group or ""
    g_ok = g == "" or product["gender"] in ("any", g)
    a_ok = a in ("", "any") or product["age_group"] in ("any", a)
    return g_ok and a_ok


def recommend(prefs: dict | None, limit: int = 6, *,
              gender: str | None = None, age_group: str | None = None,
              interests: list[str] | None = None) -> list[dict]:
    """Products filtered by gender/age group, scored by interest overlap.

    By default filters by the shopper's own profile. Pass gender/age_group/interests
    to shop for SOMEONE ELSE (e.g. their kid or wife) — those override the profile
    for this call only. Returns product dicts annotated with `matched_interests`.
    """
    prefs = prefs or {}
    g = gender if gender is not None else prefs.get("gender", "")
    ag = age_group if age_group is not None else prefs.get("age_group", "any")
    ints = {i.lower() for i in (interests if interests is not None else prefs.get("interests", []))}

    scored = []
    for p in PRODUCTS:
        if not _matches(p, g, ag):
            continue
        overlap = sorted(ints & {t.lower() for t in p["interests"]})
        scored.append((len(overlap), p, overlap))
    # highest interest overlap first, then cheaper first as a tiebreak
    scored.sort(key=lambda t: (-t[0], t[1]["price_usd"]))
    return [{**p, "matched_interests": overlap} for _, p, overlap in scored[:limit]]


def get(product_id_or_name: str) -> dict | None:
    q = (product_id_or_name or "").strip().lower()
    for p in PRODUCTS:
        if p["id"] == q or p["name"].lower() == q:
            return p
    # loose contains match on name
    for p in PRODUCTS:
        if q and q in p["name"].lower():
            return p
    return None


def format_price(price_usd: float, prefs: dict | None) -> str:
    prefs = prefs or {}
    symbol = prefs.get("currency_symbol", "$")
    rate = prefs.get("currency_rate", 1.0)
    amount = round(price_usd * rate)
    return f"{symbol}{amount:,}"


def recommend_markdown(prefs: dict | None, limit: int = 6, *,
                       gender: str | None = None, age_group: str | None = None,
                       interests: list[str] | None = None, for_label: str | None = None) -> str:
    """A ready-to-show markdown list of recommendations with real prices and a ⭐
    highlight on interest matches. Prices always use the shopper's currency.

    Pass gender/age_group/interests + for_label to show picks for someone else
    (a gift recipient); a header names who they're for."""
    picks = recommend(prefs, limit, gender=gender, age_group=age_group, interests=interests)
    if not picks:
        who = f" for {for_label}" if for_label else " for your profile"
        return f"_No products match{who} yet._"
    header = f"Picks for **{for_label}**:\n" if for_label else ""
    lines = []
    for i, p in enumerate(picks, 1):
        price = format_price(p["price_usd"], prefs)
        star = " ⭐" if p["matched_interests"] else ""
        why = f"  — _matches interest in {', '.join(p['matched_interests'])}_" if p["matched_interests"] else ""
        lines.append(f"{i}. {p.get('emoji', '🛍️')} **{p['name']}** ({p['category']}) — {price}{star}{why}")
    return header + "\n".join(lines)
