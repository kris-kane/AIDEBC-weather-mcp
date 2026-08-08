"""
Weather-forecast MCP server.

Exposes weather tools over MCP (Model Context Protocol) so a Databricks
Agent Bricks agent can call them like any other tool:
    - get_current_weather(location)
    - get_forecast(location, days)
    - get_travel_recommendation(location, date)

These tools are backed by Open-Meteo (see weather_broker.py) - a free,
key-free weather API, so there's no secrets management needed for this
data source. Unlike the alpaca-paper-trading MCP server, these tools are
stateless reads: no Lakebase, no embedding model, no user identity -
just HTTP calls to Open-Meteo and a small amount of derived logic on top.

Deploy this as its own Databricks App (same app.yaml + FastMCP entrypoint
pattern as alpaca_mcp_server.py), separate from any dashboard app, so an
Agent Bricks agent (or any MCP client) can register its URL as an
external MCP server.

Run locally:
    python weather_mcp_server.py
"""

import logging
import os

from fastmcp import FastMCP

import weather_broker as broker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp-server")

mcp = FastMCP("weather-mcp-server")


@mcp.tool
def get_current_weather(location: str) -> dict:
    """
    Get current weather conditions for a location.

    Args:
        location: A place name, e.g. "Chicago", "Austin, TX", "Paris, France".

    Returns:
        A dict with location, temperature_f, feels_like_f, humidity_pct,
        wind_mph, conditions. On failure, returns {"error": <message>}
        instead of raising, so the calling agent gets a clean signal it
        can react to (e.g. ask the user to clarify) rather than a stack trace.
    """
    try:
        return broker.fetch_current_weather(location)
    except (broker.LocationNotFoundError, broker.WeatherAPIError) as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error fetching current weather for {location!r}")
        return {"error": f"Unexpected error: {e}"}


@mcp.tool
def get_forecast(location: str, days: int = 3) -> dict:
    """
    Get a multi-day weather forecast for a location.

    Args:
        location: A place name, e.g. "Chicago", "Austin, TX".
        days: Number of days to forecast, 1-16 (default 3). Out-of-range
            values are clamped rather than rejected.

    Returns:
        A dict with location and days (a list of {date, high_f, low_f,
        precipitation_chance_pct, conditions}). On failure, returns
        {"error": <message>}.
    """
    try:
        return broker.fetch_forecast(location, days=days)
    except (broker.LocationNotFoundError, broker.WeatherAPIError) as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error fetching forecast for {location!r}")
        return {"error": f"Unexpected error: {e}"}


@mcp.tool
def get_travel_recommendation(location: str, date: str) -> dict:
    """
    Get an umbrella/jacket recommendation for a specific date - a judgment
    call derived from the forecast, not just a passthrough of raw numbers.

    Recommends an umbrella if precipitation chance is above 40%, and a
    jacket if the day's low temperature is under 55F (see
    weather_broker.get_travel_recommendation for the exact thresholds).

    Args:
        location: A place name, e.g. "Chicago", "Austin, TX".
        date: Date in YYYY-MM-DD format, must fall within the next 16 days.

    Returns:
        A dict with location, date, forecast, bring_umbrella, bring_jacket,
        and summary (a plain-language explanation). On failure, returns
        {"error": <message>}.
    """
    try:
        return broker.get_travel_recommendation(location, date)
    except (broker.LocationNotFoundError, broker.WeatherAPIError, broker.DateNotInForecastError) as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error building travel recommendation for {location!r} on {date!r}")
        return {"error": f"Unexpected error: {e}"}


if __name__ == "__main__":
    # Databricks Apps route external HTTP traffic to this port via app.yaml;
    # "http" is the transport Databricks' MCP client/gateway expects for a
    # custom MCP server hosted as a Databricks App.
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    mcp.run(transport="http", host="0.0.0.0", port=port)