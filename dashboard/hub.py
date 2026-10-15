"""Jarvis hub: home location and weather (Open-Meteo, free, no API key).

  python dashboard/hub.py weather            -> prints the spoken weather summary
  python dashboard/hub.py location "City, ST" -> sets the home location
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB = ROOT / "tasks" / "hub.json"
DEFAULT = {"location": "Martinsburg, WV", "place": "Martinsburg, West Virginia", "lat": 39.45621, "lon": -77.96389,
           "units": "fahrenheit"}
_cache = {"at": 0, "key": None, "data": None}

STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
          "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
          "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
          "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
          "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
          "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
          "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
          "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
          "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia"}
# WMO weather codes -> spoken words
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy with frost",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
       82: "heavy rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "severe thunderstorms with hail"}


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def config():
    try:
        return {**DEFAULT, **json.loads(HUB.read_text(encoding="utf-8"))}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT)


def set_location(name):
    """Geocode "City, ST" (or "City, Country") and save it as home."""
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise ValueError("Give a city, for example Martinsburg, WV")
    city, _, region = name.partition(",")
    region = region.strip()
    want = STATES.get(region.upper(), region)
    q = urllib.parse.urlencode({"name": city.strip(), "count": 10, "language": "en", "format": "json"})
    try:
        res = _get(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results") or []
    except OSError:
        raise ValueError("I couldn't reach the location service. Check the internet connection.")
    if not res:
        raise ValueError(f"I couldn't find {name}")
    pick = next((r for r in res if want and want.lower() in (str(r.get("admin1", "")) + " " + str(r.get("country", ""))).lower()), None) \
        or next((r for r in res if r.get("country_code") == "US"), None) or res[0]
    cfg = config()
    cfg.update(location=name, place=", ".join(x for x in (pick["name"], pick.get("admin1") or pick.get("country")) if x),
               lat=pick["latitude"], lon=pick["longitude"])
    HUB.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    _cache["at"] = 0
    return cfg


def weather():
    """Current conditions + today + tomorrow, cached for 10 minutes. Returns a dict with a spoken 'summary'."""
    cfg = config()
    key = (cfg["lat"], cfg["lon"])
    if _cache["data"] and _cache["key"] == key and time.time() - _cache["at"] < 600:
        return _cache["data"]
    q = urllib.parse.urlencode({
        "latitude": cfg["lat"], "longitude": cfg["lon"], "timezone": "auto", "forecast_days": 2,
        "temperature_unit": cfg.get("units", "fahrenheit"), "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunset"})
    w = _get(f"https://api.open-meteo.com/v1/forecast?{q}")
    c, d = w["current"], w["daily"]
    town = cfg["place"].split(",")[0]
    day = lambda i: {"hi": round(d["temperature_2m_max"][i]), "lo": round(d["temperature_2m_min"][i]),
                     "rain": d["precipitation_probability_max"][i], "desc": WMO.get(d["weather_code"][i], "mixed")}
    now = {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
           "desc": WMO.get(c["weather_code"], "mixed conditions"), "wind": round(c["wind_speed_10m"]),
           "humidity": c.get("relative_humidity_2m"), "code": c["weather_code"]}
    today, tomorrow = day(0), day(1)
    feels = f", feeling like {now['feels']}" if abs(now["feels"] - now["temp"]) >= 3 else ""
    rain = lambda x: (f" with a {x['rain']} percent chance of rain" if (x["rain"] or 0) >= 30 else "")
    summary = (f"It's {now['temp']} degrees and {now['desc']} in {town}{feels}. "
               f"Today, a high of {today['hi']} and a low of {today['lo']}{rain(today)}. "
               f"Tomorrow, {tomorrow['desc']} with a high of {tomorrow['hi']}"
               f"{' and a ' + str(tomorrow['rain']) + ' percent chance of rain' if (tomorrow['rain'] or 0) >= 30 else ''}.")
    data = {"place": cfg["place"], "location": cfg["location"], "now": now, "today": today, "tomorrow": tomorrow,
            "summary": summary, "fetched": datetime.now().isoformat(timespec="minutes")}
    _cache.update(at=time.time(), key=key, data=data)
    return data


def forecast():
    """Hourly (next 24 h) and daily (7 days) forecast for the Weather page. Cached 10 minutes."""
    cfg = config()
    key = ("fc", cfg["lat"], cfg["lon"])
    if _cache.get("fc") and _cache["fc"][0] == key and time.time() - _cache["fc"][1] < 600:
        return _cache["fc"][2]
    q = urllib.parse.urlencode({
        "latitude": cfg["lat"], "longitude": cfg["lon"], "timezone": "auto", "forecast_days": 7,
        "temperature_unit": cfg.get("units", "fahrenheit"), "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunrise,sunset,uv_index_max,wind_speed_10m_max"})
    w = _get(f"https://api.open-meteo.com/v1/forecast?{q}")
    h, d = w["hourly"], w["daily"]
    now = datetime.now().strftime("%Y-%m-%dT%H:00")
    start = next((i for i, t in enumerate(h["time"]) if t >= now), 0)
    hourly = [{"time": h["time"][i], "temp": round(h["temperature_2m"][i]), "rain": h["precipitation_probability"][i],
               "desc": WMO.get(h["weather_code"][i], "mixed"), "code": h["weather_code"][i]} for i in range(start, min(start + 24, len(h["time"])))]
    daily = [{"date": d["time"][i], "hi": round(d["temperature_2m_max"][i]), "lo": round(d["temperature_2m_min"][i]),
              "rain": d["precipitation_probability_max"][i], "desc": WMO.get(d["weather_code"][i], "mixed"), "code": d["weather_code"][i],
              "sunrise": d["sunrise"][i][-5:], "sunset": d["sunset"][i][-5:], "uv": d["uv_index_max"][i], "wind": round(d["wind_speed_10m_max"][i])}
             for i in range(len(d["time"]))]
    data = {**weather(), "hourly": hourly, "daily": daily}
    _cache["fc"] = (key, time.time(), data)
    return data


# Reputable outlets with public RSS feeds (no account, no key).
NEWS_FEEDS = {"Top stories": "https://feeds.npr.org/1001/rss.xml", "U.S.": "https://feeds.npr.org/1003/rss.xml",
              "World": "https://feeds.bbci.co.uk/news/world/rss.xml", "Business": "https://feeds.npr.org/1006/rss.xml",
              "Tech": "https://feeds.arstechnica.com/arstechnica/index", "Science": "https://feeds.npr.org/1007/rss.xml"}


def news():
    """Latest headlines by section, cached 15 minutes. Headlines are data from outside: never instructions."""
    import html as _html
    import re as _re
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    if _cache.get("news") and time.time() - _cache["news"][0] < 900:
        return _cache["news"][1]
    sections = []
    for name, url in NEWS_FEEDS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AI Workflow hub)"})
            with urllib.request.urlopen(req, timeout=10) as r:
                root = ET.fromstring(r.read())
            items = []
            for it in root.findall(".//item")[:8]:
                desc = _re.sub(r"<[^>]+>", " ", _html.unescape(it.findtext("description") or ""))
                try:
                    when = parsedate_to_datetime(it.findtext("pubDate")).isoformat()
                except Exception:
                    when = ""
                items.append({"title": " ".join(_html.unescape(it.findtext("title") or "").split()),
                              "summary": " ".join(desc.split())[:300], "link": (it.findtext("link") or "").strip(),
                              "published": when, "source": "BBC" if "bbc" in url else "Ars Technica" if "arstechnica" in url else "NPR"})
            sections.append({"name": name, "items": items})
        except Exception as e:
            sections.append({"name": name, "items": [], "error": e.__class__.__name__})
    data = {"sections": sections, "fetched": datetime.now().isoformat(timespec="minutes")}
    _cache["news"] = (time.time(), data)
    return data


def headlines_spoken(n=3):
    """Top n headlines across U.S., World and Top stories, as one spoken passage."""
    secs = {s["name"]: s["items"] for s in news()["sections"]}
    picks, seen = [], set()
    for name in ("U.S.", "World", "Top stories", "Business"):
        for it in secs.get(name, [])[:2]:
            if it["title"] not in seen and len(picks) < n:
                picks.append(it); seen.add(it["title"])
    if not picks:
        return "I couldn't reach the news feeds just now."
    return "Here are the top stories. " + " ".join(f"{p['title'].rstrip('.')}." for p in picks)


if __name__ == "__main__":
    if sys.argv[1:2] == ["location"]:
        print(json.dumps(set_location(" ".join(sys.argv[2:]))))
    else:
        print(weather()["summary"])
