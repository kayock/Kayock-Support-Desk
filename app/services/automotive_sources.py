"""
Optional online automotive lookups: VIN decoding, NHTSA recalls, and
generic OBD-II DTC definitions. Isolated here so the rest of the app
(templates, query_service, the RAG pipeline) never talks HTTP directly --
see decode_vin(), get_recalls(), and lookup_dtc() below.

Core design principle: uploaded manuals remain the highest-priority
source for anything vehicle-specific. Nothing in this module ever reads
document content, retrieved excerpts, or the vector store -- it only ever
sends the minimum identifier a lookup needs (a VIN, or a year/make/model),
and every result carries clear source attribution so the rest of the app
can keep "your manual" separate from "the internet" in what it shows.

Sources used (see README "Online Automotive Sources" for the research
that led to these choices):

- VIN decode: NHTSA vPIC API (vpic.nhtsa.dot.gov) -- official, free,
  no API key, no registration, no rate limit for normal use.
- Recalls: NHTSA Recalls API (api.nhtsa.gov/recalls) -- official, free,
  no API key. Year/Make/Model based (no officially documented VIN-specific
  recall endpoint was found), so results are labeled as "may relate to"
  the vehicle rather than guaranteed for that exact VIN.
- Generic DTC definitions: no suitable free/stable/keyless public HTTP API
  was found (see README). Served from a small local dictionary instead
  (app/services/dtc_dictionary.py) behind this same lookup_dtc()
  interface, so a real API could be substituted later without touching
  any caller.

Every online call uses a short timeout and fails closed with a plain-
English message -- a slow/broken public API must never freeze the page
or crash the app, and must never be silently invented around.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.config import (
    NHTSA_RECALLS_BASE_URL,
    NHTSA_VPIC_BASE_URL,
    ONLINE_LOOKUP_TIMEOUT,
    RECALLS_CACHE_SECONDS,
    VIN_DECODE_CACHE_SECONDS,
)
from app.db import db_cursor
from app.services import settings_service
from app.services.dtc_dictionary import GENERIC_DTC_DEFINITIONS
from app.services.vehicle import is_valid_vin, normalize_vin

OFFLINE_MESSAGE = "Online lookup is currently unavailable. I can still search your uploaded manuals."


# --------------------------------------------------------------- settings --

def online_sources_enabled() -> bool:
    return settings_service.get("online_sources_enabled") == "1"


def vin_lookup_enabled() -> bool:
    return online_sources_enabled() and settings_service.get("online_vin_lookup_enabled") == "1"


def recalls_enabled() -> bool:
    return online_sources_enabled() and settings_service.get("online_recalls_enabled") == "1"


# ------------------------------------------------------------------ cache --
# Plain SQLite, same as everything else in Kayock -- no Redis, no service.

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_get(cache_key: str, max_age_seconds: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT response_json, fetched_at FROM online_cache WHERE cache_key = ?", (cache_key,))
        row = cur.fetchone()
    if row is None:
        return None
    try:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
    except ValueError:
        return None
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age > max_age_seconds:
        return None
    try:
        data = json.loads(row["response_json"])
    except json.JSONDecodeError:
        return None
    data["fetched_at"] = row["fetched_at"]
    data["cache_hit"] = True
    return data


def _cache_set(cache_key: str, source: str, request_type: str, identifier: str, data: dict) -> None:
    payload = {k: v for k, v in data.items() if k not in ("cache_hit",)}
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO online_cache (cache_key, source, request_type, identifier, response_json, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET
                 response_json = excluded.response_json, fetched_at = excluded.fetched_at""",
            (cache_key, source, request_type, identifier, json.dumps(payload), _now_iso()),
        )


def _cache_fetched_at(cache_key: str) -> str | None:
    with db_cursor() as cur:
        cur.execute("SELECT fetched_at FROM online_cache WHERE cache_key = ?", (cache_key,))
        row = cur.fetchone()
    return row["fetched_at"] if row else None


# -------------------------------------------------------------- HTTP core --

def _get_json(url: str, timeout: int = ONLINE_LOOKUP_TIMEOUT) -> tuple[dict | None, str | None]:
    """Returns (data, error). error is a short, plain-English message safe
    to show a user; the real exception detail is never surfaced to the UI."""
    request = urllib.request.Request(url, headers={"User-Agent": "Kayock-Support-Desk/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return None, f"The online source returned an error (HTTP {exc.code})."
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return None, "The online source took too long to respond."
        return None, "Could not reach the online source (no internet connection?)."
    except TimeoutError:
        return None, "The online source took too long to respond."
    except Exception:  # noqa: BLE001 -- never let a raw exception reach the UI
        return None, "The online source could not be reached."

    if status != 200:
        return None, f"The online source returned an error (HTTP {status})."
    try:
        data = json.loads(raw.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None, "The online source returned data Kayock couldn't understand."
    if not isinstance(data, dict):
        return None, "The online source returned data Kayock couldn't understand."
    return data, None


# --------------------------------------------------------------- VIN decode

# vPIC fields worth surfacing -- mapped to Kayock's own vehicle profile
# field names, so callers get something ready to compare/apply directly.
_VPIC_FIELD_MAP = {
    "vehicle_year": "ModelYear",
    "vehicle_make": "Make",
    "vehicle_model": "Model",
    "vehicle_trim": "Trim",
}


def decode_vin(vin: str, force_refresh: bool = False) -> dict:
    """Returns a normalized result:
    {
      "ok": bool,
      "error": str | None,
      "source": "NHTSA vPIC",
      "fetched_at": iso str | None,
      "cache_hit": bool,
      "suggested_fields": {"vehicle_year": ..., "vehicle_make": ..., ...},
      "raw": {...a few extra useful raw values...},
    }
    Only the VIN itself is ever sent -- no manuals, no other device data.
    """
    vin = normalize_vin(vin)
    if not is_valid_vin(vin):
        return _error_result("NHTSA vPIC", "That doesn't look like a valid 17-character VIN.")

    if not vin_lookup_enabled():
        return _error_result("NHTSA vPIC", "Online VIN decode is turned off in Settings.")

    cache_key = f"vpic:vin_decode:{vin}"
    if not force_refresh:
        cached = _cache_get(cache_key, VIN_DECODE_CACHE_SECONDS)
        if cached is not None:
            return cached

    url = f"{NHTSA_VPIC_BASE_URL}/vehicles/DecodeVinValues/{vin}?format=json"
    data, error = _get_json(url)
    if error:
        return _error_result("NHTSA vPIC", error)

    try:
        results = data.get("Results") or []
        record = results[0] if results else {}
    except (AttributeError, IndexError):
        record = {}

    error_code = str(record.get("ErrorCode", "")).strip()
    # vPIC's ErrorCode "0" means clean decode; other codes still often
    # return partial/useful data, so only treat it as a hard failure when
    # essentially nothing came back.
    if not record or (not record.get("Make") and not record.get("ModelYear")):
        return _error_result("NHTSA vPIC", "NHTSA did not return usable information for this VIN.")

    suggested = {field: (record.get(src) or "").strip() for field, src in _VPIC_FIELD_MAP.items()}
    suggested["vehicle_engine"] = _format_engine(record)
    suggested["vehicle_drivetrain"] = (record.get("DriveType") or "").strip()
    suggested = {k: v for k, v in suggested.items() if v}

    result = {
        "ok": True,
        "error": None,
        "source": "NHTSA vPIC",
        "fetched_at": _now_iso(),
        "cache_hit": False,
        "suggested_fields": suggested,
        "raw": {
            "vehicle_type": record.get("VehicleType", ""),
            "body_class": record.get("BodyClass", ""),
            "plant_country": record.get("PlantCountry", ""),
            "error_code": error_code,
            "error_text": record.get("ErrorText", ""),
        },
    }
    _cache_set(cache_key, "NHTSA vPIC", "vin_decode", vin, result)
    return result


def _format_engine(record: dict) -> str:
    parts = []
    displacement = (record.get("DisplacementL") or "").strip()
    if displacement:
        parts.append(f"{displacement}L")
    cylinders = (record.get("EngineCylinders") or "").strip()
    if cylinders:
        parts.append(f"{cylinders}-cyl")
    fuel = (record.get("FuelTypePrimary") or "").strip()
    if fuel:
        parts.append(fuel)
    return " ".join(parts)


# ------------------------------------------------------------------ recalls

def get_recalls(make: str, model: str, model_year: str, force_refresh: bool = False) -> dict:
    """Returns:
    {
      "ok": bool,
      "error": str | None,
      "source": "NHTSA Recalls",
      "fetched_at": iso str | None,
      "cache_hit": bool,
      "vin_specific": False,  # this endpoint is Y/M/M-based, not per-VIN
      "recalls": [{"campaign_number", "component", "summary", "consequence",
                    "remedy", "manufacturer"}],
    }
    Only year/make/model are ever sent -- never the VIN, never manuals.
    """
    make = (make or "").strip()
    model = (model or "").strip()
    model_year = (model_year or "").strip()
    if not (make and model and model_year):
        return _error_result("NHTSA Recalls", "Add the vehicle's year, make, and model to check recalls.")

    if not recalls_enabled():
        return _error_result("NHTSA Recalls", "Online recall lookup is turned off in Settings.")

    identifier = f"{model_year}|{make}|{model}".lower()
    cache_key = f"nhtsa:recalls:{identifier}"
    if not force_refresh:
        cached = _cache_get(cache_key, RECALLS_CACHE_SECONDS)
        if cached is not None:
            return cached

    from urllib.parse import urlencode

    query = urlencode({"make": make, "model": model, "modelYear": model_year})
    url = f"{NHTSA_RECALLS_BASE_URL}?{query}"
    data, error = _get_json(url)
    if error:
        return _error_result("NHTSA Recalls", error)

    raw_results = data.get("results")
    if raw_results is None:
        return _error_result("NHTSA Recalls", "NHTSA did not return usable recall information for this vehicle.")

    recalls = [
        {
            "campaign_number": r.get("NHTSACampaignNumber", ""),
            "component": r.get("Component", ""),
            "summary": r.get("Summary", ""),
            "consequence": r.get("Consequence", ""),
            "remedy": r.get("Remedy", ""),
            "manufacturer": r.get("Manufacturer", ""),
            "report_received_date": r.get("ReportReceivedDate", ""),
        }
        for r in raw_results
    ]

    result = {
        "ok": True,
        "error": None,
        "source": "NHTSA Recalls",
        "fetched_at": _now_iso(),
        "cache_hit": False,
        "vin_specific": False,
        "recalls": recalls,
    }
    _cache_set(cache_key, "NHTSA Recalls", "recalls", identifier, result)
    return result


def recalls_last_checked(make: str, model: str, model_year: str) -> str | None:
    identifier = f"{model_year}|{make}|{model}".lower().strip()
    return _cache_fetched_at(f"nhtsa:recalls:{identifier}")


# --------------------------------------------------------- generic DTC lookup

_DTC_CATEGORY_NAMES = {"P": "Powertrain", "B": "Body", "C": "Chassis", "U": "Network"}


def lookup_dtc(code: str) -> dict:
    """Local/offline lookup -- see module docstring for why. Always
    available regardless of the online-sources setting or internet
    connectivity, since it makes no network call.

    Returns:
    {
      "ok": bool,             # True if the code was recognized at all
      "code": "P0302",
      "definition": "Cylinder 2 Misfire Detected" | None,
      "category": "Powertrain" | None,
      "source": "Standard OBD-II definition (offline reference)",
    }
    """
    normalized = (code or "").strip().upper()
    definition = GENERIC_DTC_DEFINITIONS.get(normalized)
    category = _DTC_CATEGORY_NAMES.get(normalized[:1]) if normalized[:1] in _DTC_CATEGORY_NAMES else None

    return {
        "ok": definition is not None,
        "code": normalized,
        "definition": definition,
        "category": category,
        "source": "Standard OBD-II definition (offline reference)",
    }


# ------------------------------------------------------------------- utils

def _error_result(source: str, message: str) -> dict:
    return {
        "ok": False,
        "error": message,
        "source": source,
        "fetched_at": None,
        "cache_hit": False,
    }
