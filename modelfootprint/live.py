"""Live-signal layer: real-time grid carbon intensity and weather-driven
water-usage estimates (LIVE_SIGNAL_ROADMAP.md items 1 and 3).

Hard rule: the statusline NEVER fetches. Network calls happen only in
`refresh()` (invoked by `python3 -m modelfootprint refresh` or the /footprint
command); results land in an hourly-TTL cache file that everything else reads
with `read_cached()`. No key / offline / API error -> partial or absent
snapshot with the failure recorded in snapshot["errors"] — callers fall back
to the static coefficients and say so. Never a silent substitution.

Signals and their honest units:
  - Electricity Maps: absolute average grid CI in gCO2e/kWh (+ 24h forecast
    where the account's plan allows). This is the only signal allowed to
    replace the static CI number.
  - WattTime (optional): marginal-emissions *percentile* (0-100) for the
    region. A percentile is not g/kWh — it is used for when-to-prompt advice
    only and never mixed into the absolute carbon figure.
  - Open-Meteo (keyless): temperature/humidity -> wet-bulb (Stull) -> a
    MODELED economizer-threshold ramp for WUE_site (METHODOLOGY.md §4.1).

Config (env):
  FOOTPRINT_SITE       one of SITE_PRESETS below (sets zone+coords+climate)
  FOOTPRINT_ZONE       Electricity Maps zone id (overrides site's zone)
  FOOTPRINT_LAT/LON    datacenter coordinates for weather (override site's)
  FOOTPRINT_EM_TOKEN   Electricity Maps API token
  FOOTPRINT_WT_USER / FOOTPRINT_WT_PASS   WattTime credentials (optional)
  FOOTPRINT_CACHE      cache file path (default ~/.cache/modelfootprint/live.json)
"""
import json
import math
import os
import time
import urllib.error
import urllib.request

DEFAULT_TTL_S = 3600  # refresh cadence; free-tier friendly
MAX_AGE_S = 3 * 3600  # beyond this a snapshot is unusable, not just stale
HTTP_TIMEOUT_S = 10

# Representative datacenter metros: coordinates for the weather signal, the
# grid zone they draw from, and the climate class mapping into
# coefficients.json region_presets. Locations/config, not coefficients.
SITE_PRESETS = {
    "virginia": {"zone": "US-MIDA-PJM", "lat": 39.04, "lon": -77.49, "region": "temperate"},
    "iowa": {"zone": "US-MIDW-MISO", "lat": 41.26, "lon": -95.86, "region": "temperate"},
    "oregon": {"zone": "US-NW-PACW", "lat": 45.60, "lon": -121.18, "region": "temperate"},
    "texas": {"zone": "US-TEX-ERCO", "lat": 32.78, "lon": -96.80, "region": "hot_arid"},
    "phoenix": {"zone": "US-SW-AZPS", "lat": 33.45, "lon": -112.07, "region": "hot_arid"},
    "california": {"zone": "US-CAL-CISO", "lat": 37.24, "lon": -120.88, "region": "temperate"},
    "dublin": {"zone": "IE", "lat": 53.35, "lon": -6.26, "region": "cool_humid"},
    "amsterdam": {"zone": "NL", "lat": 52.37, "lon": 4.90, "region": "cool_humid"},
    "frankfurt": {"zone": "DE", "lat": 50.11, "lon": 8.68, "region": "cool_humid"},
    "singapore": {"zone": "SG", "lat": 1.35, "lon": 103.82, "region": "hot_humid"},
}


def cache_path():
    return os.environ.get("FOOTPRINT_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelfootprint", "live.json"
    )


def site_config():
    """Resolve zone / coordinates / region class from env. Returns a dict;
    fields are None when unconfigured (each signal degrades independently)."""
    site = (os.environ.get("FOOTPRINT_SITE") or "").lower()
    preset = SITE_PRESETS.get(site, {})
    lat = os.environ.get("FOOTPRINT_LAT") or preset.get("lat")
    lon = os.environ.get("FOOTPRINT_LON") or preset.get("lon")
    return {
        "site": site or None,
        "zone": os.environ.get("FOOTPRINT_ZONE") or preset.get("zone"),
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "region": preset.get("region"),
    }


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def wet_bulb_stull(temp_C, rh_pct):
    """Stull (2011) wet-bulb approximation from dry-bulb temp and RH%.
    Valid roughly 5-99% RH, -20..50 C — fine for this MODELED use."""
    T, rh = float(temp_C), float(rh_pct)
    return (
        T * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(T + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def wue_from_weather(temp_C, wue_preset, threshold_C=29.4, ramp_C=6.0):
    """MODELED economizer-threshold ramp (METHODOLOGY.md §4.1): below
    (threshold - ramp) the site runs free/dry cooling near the preset's low
    bound; above the threshold evaporative cooling is fully engaged near the
    high bound; linear in between. threshold_C = 29.4 (85F), the disclosed
    Microsoft economizer setpoint. Facility-level truth is not public — this
    stays a modeled estimate and must be labeled as such."""
    lo, hi = wue_preset["low"], wue_preset["high"]
    if temp_C >= threshold_C:
        return hi
    if temp_C <= threshold_C - ramp_C:
        return lo
    frac = (temp_C - (threshold_C - ramp_C)) / ramp_C
    return lo + frac * (hi - lo)


def _fetch_electricitymaps(zone, token, snapshot, errors):
    headers = {"auth-token": token}
    base = "https://api.electricitymap.org/v3"
    try:
        d = _get_json("%s/carbon-intensity/latest?zone=%s" % (base, zone), headers)
        ci = d.get("carbonIntensity")
        if isinstance(ci, (int, float)):
            snapshot["ci_g_per_kwh"] = float(ci)
            snapshot["ci_source"] = "electricitymaps"
            snapshot["ci_updated_at"] = d.get("datetime")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        errors.append("electricitymaps latest: %s" % e)
    try:
        d = _get_json("%s/carbon-intensity/forecast?zone=%s" % (base, zone), headers)
        fc = [
            {"t": p.get("datetime"), "ci": float(p["carbonIntensity"])}
            for p in d.get("forecast", [])
            if isinstance(p.get("carbonIntensity"), (int, float))
        ]
        if fc:
            snapshot["ci_forecast"] = fc
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        # Forecast endpoint is plan-gated on some accounts: degrade quietly
        # to latest-only, but still record why.
        errors.append("electricitymaps forecast: %s" % e)


def _fetch_watttime(user, password, snapshot, errors):
    """WattTime free tier exposes a marginal-emissions percentile index
    (0-100), not g/kWh. Stored separately; used for timing advice only."""
    import base64

    try:
        req = urllib.request.Request(
            "https://api.watttime.org/login",
            headers={
                "Authorization": "Basic "
                + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
            },
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            token = json.loads(r.read().decode())["token"]
        d = _get_json(
            "https://api.watttime.org/v3/signal-index?region=%s&signal_type=co2_moer"
            % (os.environ.get("FOOTPRINT_WT_REGION") or "CAISO_NORTH"),
            {"Authorization": "Bearer " + token},
        )
        pts = d.get("data") or []
        if pts and isinstance(pts[0].get("value"), (int, float)):
            snapshot["moer_percentile"] = float(pts[0]["value"])
            snapshot["moer_source"] = "watttime signal-index (marginal percentile, NOT g/kWh)"
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        errors.append("watttime: %s" % e)


def _fetch_weather(lat, lon, wue_preset, snapshot, errors):
    try:
        d = _get_json(
            "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
            "&current=temperature_2m,relative_humidity_2m&forecast_days=2"
            "&hourly=temperature_2m,relative_humidity_2m" % (lat, lon)
        )
        cur = d.get("current") or {}
        temp = cur.get("temperature_2m")
        rh = cur.get("relative_humidity_2m")
        if isinstance(temp, (int, float)) and isinstance(rh, (int, float)):
            snapshot["weather"] = {
                "temp_C": float(temp),
                "rh_pct": float(rh),
                "wet_bulb_C": round(wet_bulb_stull(temp, rh), 2),
            }
            if wue_preset:
                snapshot["wue_site_L_per_kWh"] = round(
                    wue_from_weather(float(temp), wue_preset), 4
                )
                snapshot["wue_note"] = (
                    "MODELED economizer-threshold ramp from live dry-bulb temp"
                )
        hourly = d.get("hourly") or {}
        temps = hourly.get("temperature_2m") or []
        times = hourly.get("time") or []
        if temps and times and wue_preset:
            snapshot["wue_forecast"] = [
                {"t": t, "wue": round(wue_from_weather(float(v), wue_preset), 4)}
                for t, v in list(zip(times, temps))[:36]
                if isinstance(v, (int, float))
            ]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        errors.append("open-meteo: %s" % e)


def refresh(coeffs=None, force=False):
    """Fetch whatever signals are configured, write the cache, and return the
    snapshot. Partial failure is normal: each signal degrades independently
    and failures are recorded in snapshot['errors']."""
    path = cache_path()
    if not force:
        cached = read_cached(max_age_s=DEFAULT_TTL_S)
        if cached is not None:
            return cached

    cfg = site_config()
    errors = []
    snapshot = {
        "fetched_at": time.time(),
        "site": cfg["site"],
        "zone": cfg["zone"],
        "region": cfg["region"],
    }

    em_token = os.environ.get("FOOTPRINT_EM_TOKEN")
    if cfg["zone"] and em_token:
        _fetch_electricitymaps(cfg["zone"], em_token, snapshot, errors)
    elif not em_token:
        errors.append("no FOOTPRINT_EM_TOKEN: grid CI stays static (loc-based)")
    else:
        errors.append("no FOOTPRINT_ZONE/FOOTPRINT_SITE: grid CI stays static")

    wt_user = os.environ.get("FOOTPRINT_WT_USER")
    wt_pass = os.environ.get("FOOTPRINT_WT_PASS")
    if wt_user and wt_pass:
        _fetch_watttime(wt_user, wt_pass, snapshot, errors)

    if cfg["lat"] is not None and cfg["lon"] is not None:
        wue_preset = None
        if coeffs and cfg["region"]:
            wue_preset = coeffs["region_presets"][cfg["region"]]["WUE_site_L_per_kWh"]
        elif coeffs:
            region = os.environ.get("FOOTPRINT_REGION", "temperate")
            if region in coeffs.get("region_presets", {}):
                wue_preset = coeffs["region_presets"][region]["WUE_site_L_per_kWh"]
        _fetch_weather(cfg["lat"], cfg["lon"], wue_preset, snapshot, errors)
    else:
        errors.append("no FOOTPRINT_SITE/LAT/LON: water stays on region preset")

    snapshot["errors"] = errors
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        os.replace(tmp, path)
    except OSError as e:
        snapshot["errors"].append("cache write failed: %s" % e)
    return snapshot


def read_cached(max_age_s=MAX_AGE_S):
    """Read the snapshot without any network. Returns None if missing,
    unreadable, or older than max_age_s; otherwise the snapshot with an
    'age_s' field and a 'stale' flag (older than one TTL but still usable)."""
    try:
        with open(cache_path(), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or not isinstance(
        snap.get("fetched_at"), (int, float)
    ):
        return None
    age = time.time() - snap["fetched_at"]
    if age < 0 or age > max_age_s:
        return None
    snap["age_s"] = age
    snap["stale"] = age > DEFAULT_TTL_S
    return snap
