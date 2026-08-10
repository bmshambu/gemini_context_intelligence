"""Permanent shopper profile — country, language, gender, age, interests.

Captured upfront at onboarding, stored as a single keep-latest "profile" record in
the persona tier (no expiry). Country drives the display currency; language drives
how the agent communicates.
"""
from __future__ import annotations

from . import store

# country -> (currency_code, symbol, usd_rate)
_COUNTRY_CURRENCY = {
    "india": ("INR", "₹", 83.0),
    "united states": ("USD", "$", 1.0), "usa": ("USD", "$", 1.0), "us": ("USD", "$", 1.0),
    "united kingdom": ("GBP", "£", 0.79), "uk": ("GBP", "£", 0.79),
    "canada": ("CAD", "C$", 1.36), "australia": ("AUD", "A$", 1.52),
    "germany": ("EUR", "€", 0.92), "france": ("EUR", "€", 0.92), "spain": ("EUR", "€", 0.92),
    "japan": ("JPY", "¥", 157.0), "singapore": ("SGD", "S$", 1.35),
    "uae": ("AED", "د.إ", 3.67), "united arab emirates": ("AED", "د.إ", 3.67),
}
_DEFAULT_CURRENCY = ("USD", "$", 1.0)

# currency_code -> (symbol, usd_rate) — lets a shopper override the currency
# independently of their country (e.g. India but wants prices in USD).
_CURRENCY_BY_CODE = {
    "USD": ("$", 1.0), "INR": ("₹", 83.0), "GBP": ("£", 0.79), "EUR": ("€", 0.92),
    "CAD": ("C$", 1.36), "AUD": ("A$", 1.52), "JPY": ("¥", 157.0),
    "SGD": ("S$", 1.35), "AED": ("د.إ", 3.67),
}

_PROFILE_KEY = "profile"


def currency_for(country: str) -> tuple[str, str, float]:
    return _COUNTRY_CURRENCY.get((country or "").strip().lower(), _DEFAULT_CURRENCY)


def currency_by_code(code: str) -> tuple[str, str, float] | None:
    code = (code or "").strip().upper()
    if code in _CURRENCY_BY_CODE:
        symbol, rate = _CURRENCY_BY_CODE[code]
        return code, symbol, rate
    return None


def age_group(age) -> str:
    try:
        a = int(age)
    except (TypeError, ValueError):
        return "any"
    if a < 13:
        return "kid"
    if a < 20:
        return "teen"
    if a < 60:
        return "adult"
    return "senior"


def set_preferences(user_id, country=None, language=None, gender=None, age=None,
                    interests=None, currency=None, name=None) -> dict:
    """Upsert the permanent profile, MERGING over what's already saved.

    Every arg is optional so the shopper can update a single preference anytime
    (e.g. only currency) without wiping the rest. Currency resolution:
      - explicit `currency` code (e.g. "USD") wins — lets them keep their country
        but see prices in another currency;
      - else if `country` changed, currency is recomputed from it;
      - else the saved currency is kept.
    """
    rec = dict(get_preferences(user_id) or {})
    rec.pop("key", None)
    rec.pop("_update_time", None)

    if name is not None and str(name).strip():
        rec["name"] = str(name).strip()
    if country is not None and str(country).strip():
        rec["country"] = str(country).strip()
    if language is not None and str(language).strip():
        rec["language"] = str(language).strip()
    if gender is not None and str(gender).strip():
        rec["gender"] = str(gender).strip().lower()
    if age is not None and str(age).strip():
        rec["age"] = age
    if interests is not None:  # replace the full list when provided
        rec["interests"] = [i.strip() for i in interests if i and i.strip()]

    # currency: explicit override > country-derived (only if country given) > keep
    override = currency_by_code(currency) if currency else None
    if override:
        rec["currency_code"], rec["currency_symbol"], rec["currency_rate"] = override
    elif country is not None and str(country).strip():
        rec["currency_code"], rec["currency_symbol"], rec["currency_rate"] = currency_for(country)
    elif "currency_code" not in rec:
        rec["currency_code"], rec["currency_symbol"], rec["currency_rate"] = _DEFAULT_CURRENCY

    # derived / defaults
    rec.setdefault("language", "English")
    rec.setdefault("interests", [])
    rec["age_group"] = age_group(rec.get("age"))

    store.remember(user_id, store.TIER_PERSONA, _PROFILE_KEY, rec, ttl_seconds=None)
    return rec


def get_preferences(user_id) -> dict | None:
    for r in store.recall(user_id, store.TIER_PERSONA):
        if r.get("key") == _PROFILE_KEY:
            return r
    return None


def is_complete(prefs: dict | None) -> bool:
    """Enough captured to personalize (country + age + gender give currency + filters)."""
    return bool(prefs and prefs.get("country") and prefs.get("age") not in (None, "")
                and prefs.get("gender"))
