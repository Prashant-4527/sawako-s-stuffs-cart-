"""
kaoruko/plugins/built_in/weather/__init__.py

Built-in plugin: Weather
Uses wttr.in free API — no API key required.

Example phrases:
  "What's the weather like?"
  "Is it going to rain today?"
  "Weather in Tokyo"
  "Temperature outside"
"""
from __future__ import annotations

import json
from typing import Any, Optional
from kaoruko.plugins.plugin_base import KaorukoPlugin


class Plugin(KaorukoPlugin):
    name        = "weather"
    version     = "1.0.0"
    description = "Current weather and forecast via wttr.in"
    intents     = ["GET_WEATHER", "GET_FORECAST", "GET_TEMPERATURE"]

    def handle_intent(
        self,
        intent: str,
        entities: dict[str, Any],
        session: Optional[Any] = None,
    ) -> Optional[str]:
        location = entities.get("location", "")
        return self._fetch_weather(location, intent)

    def get_example_phrases(self) -> list[str]:
        return [
            "What's the weather like?",
            "Weather in Tokyo",
            "Is it going to rain today?",
            "What's the temperature outside?",
        ]

    def _fetch_weather(self, location: str, intent: str) -> str:
        try:
            import httpx
            loc = location.replace(" ", "+") if location else "auto"
            url = f"https://wttr.in/{loc}?format=j1"

            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url)

            if resp.status_code != 200:
                return "I couldn't fetch the weather right now~"

            data = resp.json()
            current = data["current_condition"][0]

            temp_c   = current["temp_C"]
            temp_f   = current["temp_F"]
            desc     = current["weatherDesc"][0]["value"]
            feels_c  = current["FeelsLikeC"]
            humidity = current["humidity"]
            wind_kph = current["windspeedKmph"]

            loc_name = location.title() if location else "your location"

            return (
                f"In {loc_name}: {desc}. "
                f"{temp_c}°C ({temp_f}°F), feels like {feels_c}°C. "
                f"Humidity {humidity}%, wind {wind_kph} km/h~"
            )
        except ImportError:
            return "httpx is required for weather lookups~"
        except Exception as e:
            return f"I couldn't get weather info right now~ ({e})"
