"""
Adapter for the Open-Meteo API (https://open-meteo.com).

All HTTP calls and response parsing live here, kept separate from the MCP
tool functions in weather_mcp_server.py so those stay thin wrappers -
same role as alpaca_broker.py for the trading MCP server.

Open-Meteo requires no API key and no signup (~10,000 calls/day,
non-commercial use), including its own geocoder, so there's no secrets
management needed for this data source.
"""

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

TIMEOUT = 15

# WMO weather interpretation codes (https://open-meteo.com/en/docs), the
# small subset Open-Meteo actually returns for current/daily conditions.
WEATHER_CODE_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

UMBRELLA_THRESHOLD_PCT = 40  # recommend an umbrella above this precip chance
JACKET_THRESHOLD_F = 55      # recommend a jacket below this low temperature


class LocationNotFoundError(Exception):
    """Raised when Open-Meteo's geocoder can't resolve a location string."""


class WeatherAPIError(Exception):
    """Raised when the Open-Meteo forecast API call fails."""


class DateNotInForecastError(Exception):
    """Raised when the requested date falls outside the available forecast range."""


def _describe_weather_code(code: int) -> str:
    return WEATHER_CODE_DESCRIPTIONS.get(code, f"Unknown conditions (code {code})")


def geocode_location(location: str) -> dict:
    """Resolve a free-text location ('Austin, TX', 'Chicago') to lat/lon.

    Returns:
        dict with name, latitude, longitude, country.
    Raises:
        LocationNotFoundError if no match is found.
    """
    resp = requests.get(
        GEOCODE_URL,
        params={"name": location, "count": 1, "language": "en", "format": "json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results")
    if not results:
        raise LocationNotFoundError(f"Could not resolve location: {location!r}")

    top = results[0]
    display_name = top.get("name", location)
    if top.get("admin1"):
        display_name = f"{display_name}, {top['admin1']}"

    return {
        "name": display_name,
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "country": top.get("country", ""),
    }


def fetch_current_weather(location: str) -> dict:
    """Fetch current conditions for a location.

    Returns:
        dict with location, temperature_f, feels_like_f, humidity_pct,
        wind_mph, conditions.
    Raises:
        LocationNotFoundError, WeatherAPIError.
    """
    place = geocode_location(location)

    resp = requests.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise WeatherAPIError(f"Open-Meteo forecast request failed ({resp.status_code}) for {location!r}")

    current = resp.json().get("current", {})
    return {
        "location": place["name"],
        "temperature_f": current.get("temperature_2m"),
        "feels_like_f": current.get("apparent_temperature"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "wind_mph": current.get("wind_speed_10m"),
        "conditions": _describe_weather_code(current.get("weather_code", -1)),
    }


def fetch_forecast(location: str, days: int = 3) -> dict:
    """Fetch a multi-day forecast for a location.

    Args:
        location: Free-text place name.
        days: 1-16 (Open-Meteo's supported range); out-of-range values are clamped.

    Returns:
        dict with location and days: a list of
        {date, high_f, low_f, precipitation_chance_pct, conditions}.
    Raises:
        LocationNotFoundError, WeatherAPIError.
    """
    days = max(1, min(days, 16))
    place = geocode_location(location)

    resp = requests.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "temperature_unit": "fahrenheit",
            "forecast_days": days,
            "timezone": "auto",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise WeatherAPIError(f"Open-Meteo forecast request failed ({resp.status_code}) for {location!r}")

    daily = resp.json().get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_probability_max", [])
    codes = daily.get("weather_code", [])

    day_entries = [
        {
            "date": dates[i],
            "high_f": highs[i] if i < len(highs) else None,
            "low_f": lows[i] if i < len(lows) else None,
            "precipitation_chance_pct": precip[i] if i < len(precip) else None,
            "conditions": _describe_weather_code(codes[i]) if i < len(codes) else "Unknown",
        }
        for i in range(len(dates))
    ]

    return {"location": place["name"], "days": day_entries}


def get_travel_recommendation(location: str, date: str) -> dict:
    """Build an umbrella/jacket recommendation for a specific date - a
    judgment call derived from the raw forecast, not a passthrough.

    Thresholds (intentionally simple, documented here so they're easy to
    tune): recommend an umbrella if precipitation chance > 40%; recommend
    a jacket if the day's low is under 55F.

    Args:
        location: Free-text place name.
        date: "YYYY-MM-DD", must fall within the next 16 days.

    Returns:
        dict with location, date, forecast (that day's entry),
        bring_umbrella, bring_jacket, summary (plain-language explanation).
    Raises:
        LocationNotFoundError, WeatherAPIError, DateNotInForecastError.
    """
    forecast = fetch_forecast(location, days=16)
    day = next((d for d in forecast["days"] if d["date"] == date), None)
    if day is None:
        raise DateNotInForecastError(
            f"No forecast available for {date!r} at {location!r} "
            "(forecasts only cover today through the next 16 days)."
        )

    precip = day["precipitation_chance_pct"] or 0
    low = day["low_f"]
    bring_umbrella = precip > UMBRELLA_THRESHOLD_PCT
    bring_jacket = low is not None and low < JACKET_THRESHOLD_F

    summary_parts = [
        f"{day['conditions']} in {forecast['location']} on {date}, "
        f"high of {day['high_f']}F / low of {low}F."
    ]
    if bring_umbrella:
        summary_parts.append(f"Bring an umbrella - {precip}% chance of precipitation.")
    else:
        summary_parts.append(f"No umbrella needed - only {precip}% chance of precipitation.")
    if bring_jacket:
        summary_parts.append("A jacket is a good idea given the low temperature.")

    return {
        "location": forecast["location"],
        "date": date,
        "forecast": day,
        "bring_umbrella": bring_umbrella,
        "bring_jacket": bring_jacket,
        "summary": " ".join(summary_parts),
    }