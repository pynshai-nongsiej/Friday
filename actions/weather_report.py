import webbrowser
import json
import urllib.parse
import urllib.request
from urllib.parse import quote_plus


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None
):
    """Get weather details and update the FRIDAY dashboard when possible."""

    city = parameters.get("city")
    time = parameters.get("time")
    if not city or not isinstance(city, str):
        msg = "Sir, the city is missing for the weather report."
        _speak_and_log(msg, player)
        return msg

    city = city.strip()

    if not time or not isinstance(time, str):
        time = "today"
    else:
        time = time.strip()

    search_query = f"weather in {city} {time}"

    try:
        api_city = urllib.parse.quote(city)
        url = f"https://wttr.in/{api_city}?format=j1"
        with urllib.request.urlopen(url, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))

        current = (payload.get("current_condition") or [{}])[0]
        temp_c = current.get("temp_C", "--")
        feels = current.get("FeelsLikeC", "--")
        humidity = current.get("humidity", "--")
        wind = current.get("windspeedKmph", "--")
        desc_parts = current.get("weatherDesc") or [{}]
        description = desc_parts[0].get("value", "Conditions unavailable")

        msg = (
            f"The weather in {city} is {description.lower()}, {temp_c} degrees Celsius, "
            f"feels like {feels} degrees."
        )
        if player and hasattr(player, "update_weather"):
            player.update_weather(
                city=city,
                summary=description,
                temperature=temp_c,
                humidity=f"{humidity}%",
                wind=f"{wind} km/h",
                updated_at=f"Last sync: {time}",
            )
        _speak_and_log(msg, player)
    except Exception:
        search_query = f"weather in {city} {time}"
        encoded_query = quote_plus(search_query)
        fallback_url = f"https://www.google.com/search?q={encoded_query}"
        try:
            webbrowser.open(fallback_url)
        except Exception:
            msg = "I couldn't fetch the live weather right now."
            _speak_and_log(msg, player)
            return msg

        msg = f"Showing the weather for {city}, {time}."
        if player and hasattr(player, "update_weather"):
            player.update_weather(
                city=city,
                summary="Browser weather search opened",
                temperature="--",
                humidity="--",
                wind="--",
                updated_at=f"Last sync: {time}",
            )
        _speak_and_log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(
                query=search_query,
                response=msg
            )
        except Exception:
            pass  

    return msg


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"FRIDAY: {message}")
        except Exception:
            pass
