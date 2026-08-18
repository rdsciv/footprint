"""Live-signal layer: real-time grid carbon intensity and weather-driven
water-usage estimates (LIVE_SIGNAL_ROADMAP.md items 1 and 3).

Hard rule: the statusline NEVER fetches. Network calls happen only in
`refresh()` (invoked by `python3 -m modelfootprint refresh` or the /footprint
command); results land in an hourly-TTL cache file that everything else reads
with `read_cached()`. No key / offline / API error -> partial or absent
snapshot with the failure recorded in snapshot["errors"] — callers fall back
to the static coefficients and say so. Never a silent substitution.

Cache integrity rules:
  - The snapshot stores a fingerprint of the configuration that produced it
    (site/zone/coords/region/credential presence). A cache written under a
    different configuration is never reused or served.
  - Writes are atomic via a per-process unique temp file + os.replace.
  - On partial refresh failure, the previous snapshot's value for that signal
    is carried forward (marked with its own fetched-at time) rather than
    silently dropped; a refresh that obtains nothing new for a configured
    signal reports failure to the caller.

Signals and their honest units:
  - Electricity Maps: absolute average grid CI in gCO2e/kWh (+ 24h forecast
    where the account's plan allows). This is the only signal allowed to
    replace the static CI number.
  - WattTime (optional): marginal-emissions *percentile* (0-100) for an
    explicitly configured region. A percentile is not g/kWh — it is used for
    when-to-prompt advice only and never mixed into the absolute carbon
    figure. Requires FOOTPRINT_WT_REGION unless the site preset carries a
    known-good region; there is no silent default region.
  - Open-Meteo (keyless): temperature/humidity -> a two-signal MODELED
    cooling-water estimate (see wue_from_weather): dry-bulb gates the
    economizer threshold, wet-bulb scales evaporative draw inside the active
    band.

Config (env):
  FOOTPRINT_SITE       one of SITE_PRESETS below (sets zone+coords+climate)
  FOOTPRINT_ZONE       Electricity Maps zone id (overrides site's zone)
  FOOTPRINT_LAT/LON    datacenter coordinates for weather (override site's)
  FOOTPRINT_EM_TOKEN   Electricity Maps API token
  FOOTPRINT_WT_USER / FOOTPRINT_WT_PASS   WattTime credentials (optional)
  FOOTPRINT_WT_REGION  WattTime region id (required for the WattTime signal
                       unless the site preset defines one)
  FOOTPRINT_CACHE      cache file path (default ~/.cache/modelfootprint/live.json)
"""
import hashlib
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TTL_S = 3600  # refresh cadence; free-tier friendly
MAX_AGE_S = 3 * 3600  # beyond this a snapshot is unusable, not just stale
HTTP_TIMEOUT_S = 10
REFRESH_DEADLINE_S = 30  # total wall-clock budget for one refresh pass

# Representative datacenter metros: coordinates for the weather signal, the
# grid zone they draw from, and the climate class mapping into
# coefficients.json region_presets. Locations/config, not coefficients.
# wt_region is set only where a documented WattTime region id is known —
# WattTime is never given a silently-defaulted region.
SITE_PRESETS = {
    "virginia": {"zone": "US-MIDA-PJM", "lat": 39.04, "lon": -77.49, "region": "temperate"},
    "iowa": {"zone": "US-MIDW-MISO", "lat": 41.26, "lon": -95.86, "region": "temperate"},
    "oregon": {"zone": "US-NW-PACW", "lat": 45.60, "lon": -121.18, "region": "temperate"},
    "texas": {"zone": "US-TEX-ERCO", "lat": 32.78, "lon": -96.80, "region": "hot_arid"},
    "phoenix": {"zone": "US-SW-AZPS", "lat": 33.45, "lon": -112.07, "region": "hot_arid"},
    "california": {"zone": "US-CAL-CISO", "lat": 37.24, "lon": -120.88, "region": "temperate", "wt_region": "CAISO_NORTH"},
    "dublin": {"zone": "IE", "lat": 53.35, "lon": -6.26, "region": "cool_humid"},
    "amsterdam": {"zone": "NL", "lat": 52.37, "lon": 4.90, "region": "cool_humid"},
    "frankfurt": {"zone": "DE", "lat": 50.11, "lon": 8.68, "region": "cool_humid"},
    "singapore": {"zone": "SG", "lat": 1.35, "lon": 103.82, "region": "hot_humid"},
}


def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def cache_path():
    return os.environ.get("FOOTPRINT_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelfootprint", "live.json"
    )


def site_config():
    """Resolve zone / coordinates / region class / WattTime region from env.
    Returns a dict; fields are None when unconfigured (each signal degrades
    independently). Invalid lat/lon are treated as unconfigured, not fatal."""
    site = (os.environ.get("FOOTPRINT_SITE") or "").lower()
    preset = SITE_PRESETS.get(site, {})
    lat = _parse_coord(os.environ.get("FOOTPRINT_LAT"), preset.get("lat"), -90, 90)
    lon = _parse_coord(os.environ.get("FOOTPRINT_LON"), preset.get("lon"), -180, 180)
    return {
        "site": site or None,
        "zone": os.environ.get("FOOTPRINT_ZONE") or preset.get("zone"),
        "lat": lat,
        "lon": lon,
        "region": preset.get("region"),
        "wt_region": os.environ.get("FOOTPRINT_WT_REGION") or preset.get("wt_region"),
    }


def _parse_coord(env_val, preset_val, lo, hi):
    if env_val is not None:
        try:
            v = float(env_val)
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None
    v = _finite(preset_val)
    return v if v is not None and lo <= v <= hi else None


def config_fingerprint(cfg=None):
    """Stable hash of everything that changes what a snapshot means. A cache
    written under a different fingerprint is treated as absent."""
    cfg = cfg or site_config()
    material = json.dumps(
        [
            cfg.get("site"), cfg.get("zone"), cfg.get("lat"), cfg.get("lon"),
            cfg.get("region"), cfg.get("wt_region"),
            bool(os.environ.get("FOOTPRINT_EM_TOKEN")),
            bool(os.environ.get("FOOTPRINT_WT_USER") and os.environ.get("FOOTPRINT_WT_PASS")),
        ],
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


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


def wue_from_weather(temp_C, rh_pct, wue_preset,
                     threshold_C=29.4, ramp_C=6.0,
                     wb_floor_C=5.0, wb_ceil_C=25.0, wb_min_frac=0.4):
    """MODELED two-signal cooling-water estimate (METHODOLOGY.md §4.1):

    1. Economizer gate — dry-bulb. Direct-air-with-evaporative-assist fleets
       (the Microsoft-disclosed architecture) cool with outside air and
       introduce water only as ambient (dry-bulb) temperature approaches the
       economizer threshold (29.4 C / 85 F disclosed). Linear engagement over
       `ramp_C` below the threshold.
    2. Evaporative-draw intensity — wet-bulb. Within the active band, water
       consumed per unit heat rejected rises with wet-bulb temperature (the
       approach margin narrows; Ren et al.), so the draw scales from
       `wb_min_frac` at a cool wet-bulb (<= wb_floor_C) to 1.0 at
       wb_ceil_C+.

    So 30 C at 10% RH (wet-bulb ~15 C) draws measurably less water than 30 C
    at 90% RH (wet-bulb ~29 C). Facility-level thresholds and curves are not
    public — this is a labeled MODELED estimate bounded by the region
    preset's low/high, never an extrapolation beyond it."""
    lo, hi = wue_preset["low"], wue_preset["high"]
    t = float(temp_C)
    if t <= threshold_C - ramp_C:
        return lo
    gate = min((t - (threshold_C - ramp_C)) / ramp_C, 1.0)
    wb = wet_bulb_stull(t, float(rh_pct))
    intensity = (wb - wb_floor_C) / (wb_ceil_C - wb_floor_C)
    intensity = max(wb_min_frac, min(1.0, intensity))
    return lo + gate * intensity * (hi - lo)


def _fetch_electricitymaps(zone, token, snapshot, errors):
    headers = {"auth-token": token}
    base = "https://api.electricitymap.org/v3"
    q = urllib.parse.urlencode({"zone": zone})
    try:
        d = _get_json("%s/carbon-intensity/latest?%s" % (base, q), headers)
        ci = _finite(d.get("carbonIntensity")) if isinstance(d, dict) else None
        if ci is not None and ci >= 0:
            snapshot["ci_g_per_kwh"] = ci
            snapshot["ci_source"] = "electricitymaps"
            snapshot["ci_updated_at"] = d.get("datetime")
            snapshot["ci_fetched_at"] = time.time()
        else:
            errors.append("electricitymaps latest: unexpected response shape")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        errors.append("electricitymaps latest: %s" % e)
    try:
        d = _get_json("%s/carbon-intensity/forecast?%s" % (base, q), headers)
        raw = d.get("forecast", []) if isinstance(d, dict) else []
        fc = []
        for p in raw:
            if not isinstance(p, dict):
                continue
            ci = _finite(p.get("carbonIntensity"))
            if ci is not None and ci >= 0:
                fc.append({"t": p.get("datetime"), "ci": ci})
        if fc:
            snapshot["ci_forecast"] = fc
            snapshot["ci_forecast_fetched_at"] = time.time()
        else:
            errors.append("electricitymaps forecast: empty/unexpected response")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        # Forecast endpoint is plan-gated on some accounts: degrade quietly
        # to latest-only, but still record why.
        errors.append("electricitymaps forecast: %s" % e)


def _fetch_watttime(user, password, wt_region, snapshot, errors):
    """WattTime free tier exposes a marginal-emissions percentile index
    (0-100), not g/kWh. Stored separately; used for timing advice only.
    The region must be explicitly configured — advice for the wrong grid is
    worse than no advice."""
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
        q = urllib.parse.urlencode({"region": wt_region, "signal_type": "co2_moer"})
        d = _get_json("https://api.watttime.org/v3/signal-index?%s" % q,
                      {"Authorization": "Bearer " + token})
        pts = d.get("data") if isinstance(d, dict) else None
        val = _finite(pts[0].get("value")) if isinstance(pts, list) and pts and isinstance(pts[0], dict) else None
        if val is not None and 0 <= val <= 100:
            snapshot["moer_percentile"] = val
            snapshot["moer_region"] = wt_region
            snapshot["moer_source"] = "watttime signal-index (marginal percentile, NOT g/kWh)"
            snapshot["moer_fetched_at"] = time.time()
        else:
            errors.append("watttime: unexpected response shape")
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
        errors.append("watttime: %s" % e)


def _fetch_weather(lat, lon, wue_preset, snapshot, errors):
    try:
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m",
            "forecast_days": 2,
            "hourly": "temperature_2m,relative_humidity_2m",
        })
        d = _get_json("https://api.open-meteo.com/v1/forecast?%s" % q)
        cur = d.get("current") if isinstance(d, dict) else None
        temp = _finite(cur.get("temperature_2m")) if isinstance(cur, dict) else None
        rh = _finite(cur.get("relative_humidity_2m")) if isinstance(cur, dict) else None
        if temp is not None and rh is not None and 0 <= rh <= 100:
            snapshot["weather"] = {
                "temp_C": temp,
                "rh_pct": rh,
                "wet_bulb_C": round(wet_bulb_stull(temp, rh), 2),
            }
            snapshot["weather_fetched_at"] = time.time()
            if wue_preset:
                snapshot["wue_site_L_per_kWh"] = round(
                    wue_from_weather(temp, rh, wue_preset), 4
                )
                snapshot["wue_note"] = (
                    "MODELED: dry-bulb economizer gate x wet-bulb draw intensity"
                )
        else:
            errors.append("open-meteo: unexpected response shape")
        hourly = d.get("hourly") if isinstance(d, dict) else None
        temps = hourly.get("temperature_2m") if isinstance(hourly, dict) else None
        rhs = hourly.get("relative_humidity_2m") if isinstance(hourly, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        if temps and rhs and times and wue_preset:
            fc = []
            for t, tv, rv in list(zip(times, temps, rhs))[:36]:
                tvf, rvf = _finite(tv), _finite(rv)
                if tvf is None or rvf is None or not (0 <= rvf <= 100):
                    continue
                fc.append({"t": t, "wue": round(wue_from_weather(tvf, rvf, wue_preset), 4)})
            if fc:
                snapshot["wue_forecast"] = fc
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        errors.append("open-meteo: %s" % e)


# Per-signal carry-forward groups: (value keys, freshness key)
_SIGNAL_GROUPS = [
    (("ci_g_per_kwh", "ci_source", "ci_updated_at"), "ci_fetched_at"),
    (("ci_forecast",), "ci_forecast_fetched_at"),
    (("moer_percentile", "moer_region", "moer_source"), "moer_fetched_at"),
    (("weather", "wue_site_L_per_kWh", "wue_note", "wue_forecast"), "weather_fetched_at"),
]


def _read_raw_cache():
    try:
        with open(cache_path(), encoding="utf-8") as f:
            snap = json.load(f)
        return snap if isinstance(snap, dict) else None
    except (OSError, ValueError):
        return None


def _carry_forward(snapshot, previous, errors):
    """Keep the previous snapshot's value for any signal this refresh failed
    to obtain, provided it is not older than MAX_AGE_S. Failure must not
    destroy usable data."""
    if not previous:
        return
    now = time.time()
    for keys, ts_key in _SIGNAL_GROUPS:
        if snapshot.get(keys[0]) is not None:
            continue
        prev_ts = _finite(previous.get(ts_key)) or _finite(previous.get("fetched_at"))
        if prev_ts is None or now - prev_ts > MAX_AGE_S:
            continue
        if previous.get(keys[0]) is None:
            continue
        for k in keys:
            if previous.get(k) is not None:
                snapshot[k] = previous[k]
        snapshot[ts_key] = prev_ts
        errors.append("%s: carried forward from previous snapshot (age %dm)"
                      % (keys[0], (now - prev_ts) / 60))


def _write_cache(path, snapshot, errors):
    """Atomic write via a unique temp file in the target directory."""
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".live-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        errors.append("cache write failed: %s" % e)


def refresh(coeffs=None, force=False):
    """Fetch whatever signals are configured, write the cache, and return the
    snapshot. Partial failure is normal: each signal degrades independently,
    failures are recorded in snapshot['errors'], and previously-fetched
    values are carried forward rather than destroyed. The snapshot carries
    'ok': False when a configured signal produced neither a fresh nor a
    carried value."""
    cfg = site_config()
    fp = config_fingerprint(cfg)
    if not force:
        cached = read_cached(max_age_s=DEFAULT_TTL_S)
        if cached is not None:
            return cached

    started = time.time()
    errors = []
    snapshot = {
        "fetched_at": started,
        "config_fp": fp,
        "site": cfg["site"],
        "zone": cfg["zone"],
        "region": cfg["region"],
        # v1: schema lock only. No EIA/PJM fetch. Never swap CI from this field.
        "diesel_risk": "none",
    }

    def over_deadline(label):
        if time.time() - started > REFRESH_DEADLINE_S:
            errors.append("%s: skipped (refresh deadline %ds exceeded)"
                          % (label, REFRESH_DEADLINE_S))
            return True
        return False

    em_token = os.environ.get("FOOTPRINT_EM_TOKEN")
    ci_configured = bool(cfg["zone"] and em_token)
    if ci_configured and not over_deadline("electricitymaps"):
        _fetch_electricitymaps(cfg["zone"], em_token, snapshot, errors)
    elif not em_token:
        errors.append("no FOOTPRINT_EM_TOKEN: grid CI stays static (loc-based)")
    elif not cfg["zone"]:
        errors.append("no FOOTPRINT_ZONE/FOOTPRINT_SITE: grid CI stays static")

    wt_user = os.environ.get("FOOTPRINT_WT_USER")
    wt_pass = os.environ.get("FOOTPRINT_WT_PASS")
    wt_configured = bool(wt_user and wt_pass and cfg["wt_region"])
    if wt_user and wt_pass and not cfg["wt_region"]:
        errors.append("watttime: set FOOTPRINT_WT_REGION (no default region; "
                      "advice for the wrong grid is worse than none)")
    elif wt_configured and not over_deadline("watttime"):
        _fetch_watttime(wt_user, wt_pass, cfg["wt_region"], snapshot, errors)

    weather_configured = cfg["lat"] is not None and cfg["lon"] is not None
    if weather_configured and not over_deadline("open-meteo"):
        wue_preset = None
        if coeffs:
            region = cfg["region"] or os.environ.get("FOOTPRINT_REGION", "temperate")
            presets = coeffs.get("region_presets", {})
            if region in presets and not region.startswith(("_", "$")):
                wue_preset = presets[region]["WUE_site_L_per_kWh"]
        _fetch_weather(cfg["lat"], cfg["lon"], wue_preset, snapshot, errors)
    elif not weather_configured:
        errors.append("no FOOTPRINT_SITE/LAT/LON: water stays on region preset")

    previous = _read_raw_cache()
    if previous and previous.get("config_fp") == fp:
        _carry_forward(snapshot, previous, errors)

    ok = True
    if ci_configured and snapshot.get("ci_g_per_kwh") is None:
        ok = False
    if wt_configured and snapshot.get("moer_percentile") is None:
        ok = False
    if weather_configured and snapshot.get("weather") is None:
        ok = False
    snapshot["ok"] = ok
    snapshot["errors"] = errors
    _write_cache(cache_path(), snapshot, errors)
    return snapshot


def read_cached(max_age_s=MAX_AGE_S):
    """Read the snapshot without any network. Returns None if missing,
    unreadable, written under a different configuration, or older than
    max_age_s; otherwise the snapshot with an 'age_s' field, a 'stale' flag
    (older than one TTL but still usable), and non-finite numeric signal
    values scrubbed."""
    snap = _read_raw_cache()
    if snap is None:
        return None
    fetched = _finite(snap.get("fetched_at"))
    if fetched is None:
        return None
    if snap.get("config_fp") != config_fingerprint():
        return None
    age = time.time() - fetched
    if age < 0 or age > max_age_s:
        return None
    for key in ("ci_g_per_kwh", "wue_site_L_per_kWh", "moer_percentile"):
        if key in snap and _finite(snap[key]) is None:
            del snap[key]
    if not isinstance(snap.get("ci_forecast"), list):
        snap.pop("ci_forecast", None)
    snap["age_s"] = age
    snap["stale"] = age > DEFAULT_TTL_S
    return snap
