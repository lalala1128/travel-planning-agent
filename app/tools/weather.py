from datetime import date, timedelta

import httpx


GEOCODING_URL = (
    "https://geocoding-api.open-meteo.com/v1/search"
)

FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
)


def city_search_names(city: str) -> tuple[str, ...]:
    city = city.strip()
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in city)
    has_admin_suffix = city.endswith(("市", "州", "区", "县"))

    if has_chinese and not has_admin_suffix:
        return (f"{city}市", city)

    return (city,)


def describe_weather(code: int) -> str:
    if code == 0:
        return "晴"
    if code in (1, 2, 3):
        return "多云"
    if code in (45, 48):
        return "有雾"
    if code in (51, 53, 55, 56, 57):
        return "毛毛雨"
    if code in (61, 63, 65, 66, 67):
        return "降雨"
    if code in (71, 73, 75, 77):
        return "降雪"
    if code in (80, 81, 82):
        return "阵雨"
    if code in (85, 86):
        return "阵雪"
    if code in (95, 96, 99):
        return "雷暴"

    return "未知天气"


def get_coordinates(city: str) -> dict:
    location = None

    for search_name in city_search_names(city):
        try:
            response = httpx.get(
                GEOCODING_URL,
                params={
                    "name": search_name,
                    "count": 10,
                    "language": "zh",
                    "format": "json",
                },
                timeout=10,
            )
            response.raise_for_status()

        except httpx.RequestError as exc:
            raise RuntimeError(
                f"城市查询服务连接失败：{exc}"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"城市查询服务返回错误："
                f"{exc.response.status_code}"
            ) from exc

        results = response.json().get("results", [])
        if results:
            location = results[0]
            break

    if location is None:
        raise ValueError(f"没有找到城市：{city}")

    return {
        "name": location["name"],
        "country": location.get("country"),
        "admin1": location.get("admin1"),
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "timezone": location.get("timezone", "auto"),
    }


def get_weather(
    city: str,
    start_date: str,
    days: int,
) -> dict:
    if not 1 <= days <= 15:
        raise ValueError("天气查询天数必须在1到15之间")

    try:
        start = date.fromisoformat(start_date)
    except ValueError as exc:
        raise ValueError(
            "start_date 必须使用 YYYY-MM-DD 格式"
        ) from exc

    end = start + timedelta(days=days - 1)
    location = get_coordinates(city)

    try:
        response = httpx.get(
            FORECAST_URL,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": (
                    "weather_code,"
                    "temperature_2m_max,"
                    "temperature_2m_min,"
                    "precipitation_probability_max,"
                    "precipitation_sum"
                ),
                "timezone": location["timezone"],
            },
            timeout=15,
        )
        response.raise_for_status()

    except httpx.RequestError as exc:
        raise RuntimeError(
            f"天气服务连接失败：{exc}"
        ) from exc

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"天气服务返回错误："
            f"{exc.response.status_code}，"
            f"{exc.response.text}"
        ) from exc

    daily = response.json()["daily"]
    forecasts = []

    for index, forecast_date in enumerate(daily["time"]):
        weather_code = daily["weather_code"][index]

        forecasts.append(
            {
                "date": forecast_date,
                "weather": describe_weather(weather_code),
                "weather_code": weather_code,
                "temperature_max": (
                    daily["temperature_2m_max"][index]
                ),
                "temperature_min": (
                    daily["temperature_2m_min"][index]
                ),
                "precipitation_probability": (
                    daily[
                        "precipitation_probability_max"
                    ][index]
                ),
                "precipitation_sum": (
                    daily["precipitation_sum"][index]
                ),
            }
        )

    return {
        "city": location["name"],
        "country": location["country"],
        "province": location["admin1"],
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "forecasts": forecasts,
    }
