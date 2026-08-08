
Data Source: 

Open-Meteo - chosen because it needs zero credentials: no signup, no API key, no secrets management, including its own free geocoder. That let the whole pipeline get built and tested without touching Databricks secrets at all. 


Architecture: 
weather_broker.py - all HTTP calls + response parsing (Open-Meteo) weather_mcp_server.py - thin @mcp.tool wrappers, FastMCP, transport="http" requirements.txt 
app.yaml - Databricks App deployment config

Tools

Tool
Description
get_current_weather(location)
Current temperature, feels-like, humidity, wind, conditions for a place.
get_forecast(location, days=3)
Daily high/low, precipitation chance, and conditions for the next N days (1-16).
get_travel_recommendation(location, date)
Derived judgment call, not a passthrough: recommends an umbrella if precipitation chance > 40%, and a jacket if the low is under 55F, with a plain-language summary.

Set up
No secrets to configure - Open-Meteo needs no key. (If a future version adds an API that does need one - e.g. WeatherAPI.com for historical data, or NWS for severe alerts - store it as a Databricks secret and resolve it via WorkspaceClient().secrets.get_secret(), same pattern as alpaca_broker.py and this project's own lakebase.py, rather than hardcoding it.)
Deployed same way as the alpaca MCP server: push this folder to a Databricks App using app.yaml above. 
