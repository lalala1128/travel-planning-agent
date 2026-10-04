from math import asin, cos, radians, sin, sqrt

import httpx

from ..schemas import Attraction
from .weather import get_coordinates


OVERPASS_URLS = (
    "https://z.overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
OVERPASS_HEADERS = {
    "User-Agent": "TravelPlanningAgent/0.5 (learning-project)"
}


def fetch_overpass_elements(query: str) -> list[dict]:
    errors = []

    for url in OVERPASS_URLS:
        try:
            response = httpx.post(
                url,
                data={"data": query},
                headers=OVERPASS_HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            return response.json().get("elements", [])
        except httpx.RequestError as exc:
            errors.append(f"{url}: {exc}")
        except httpx.HTTPStatusError as exc:
            errors.append(
                f"{url}: HTTP {exc.response.status_code}"
            )
        except ValueError as exc:
            errors.append(f"{url}: 响应不是有效 JSON ({exc})")

    raise RuntimeError(
        "景点服务暂时不可用：" + "；".join(errors)
    )


def haversine_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    latitude_delta = radians(latitude2 - latitude1)
    longitude_delta = radians(longitude2 - longitude1)
    latitude1_radians = radians(latitude1)
    latitude2_radians = radians(latitude2)

    value = (
        sin(latitude_delta / 2) ** 2
        + cos(latitude1_radians)
        * cos(latitude2_radians)
        * sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * asin(sqrt(value))


def attraction_category(tags: dict) -> str:
    if tags.get("tourism"):
        return tags["tourism"]
    if tags.get("historic"):
        return f"historic:{tags['historic']}"
    if tags.get("amenity") == "restaurant":
        return "restaurant"
    return "attraction"


def parse_attractions(
    elements: list[dict],
    center_latitude: float,
    center_longitude: float,
    interests: list[str],
    limit: int,
) -> list[Attraction]:
    interest_text = " ".join(interests).lower()
    attractions = []
    seen_names = set()

    for element in elements:
        tags = element.get("tags", {})
        name = tags.get("name:zh") or tags.get("name")
        center = element.get("center", {})
        latitude = element.get("lat", center.get("lat"))
        longitude = element.get("lon", center.get("lon"))

        if not name or latitude is None or longitude is None:
            continue
        if name in seen_names:
            continue

        seen_names.add(name)
        category = attraction_category(tags)
        searchable = " ".join(
            str(value).lower() for value in tags.values()
        )
        interest_score = sum(
            1 for interest in interests
            if interest.lower() in searchable
        )

        if "美食" in interest_text and category == "restaurant":
            interest_score += 2
        if "历史" in interest_text and category.startswith("historic"):
            interest_score += 2

        distance = haversine_km(
            center_latitude,
            center_longitude,
            float(latitude),
            float(longitude),
        )
        attractions.append(
            (
                interest_score,
                distance,
                Attraction(
                    name=name,
                    category=category,
                    latitude=float(latitude),
                    longitude=float(longitude),
                    distance_from_center_km=round(distance, 2),
                ),
            )
        )

    attractions.sort(key=lambda item: (-item[0], item[1]))
    restaurant_limit = max(1, limit // 3)
    restaurant_count = 0
    selected = []

    for _, _, attraction in attractions:
        if attraction.category == "restaurant":
            if restaurant_count >= restaurant_limit:
                continue
            restaurant_count += 1

        selected.append(attraction)
        if len(selected) == limit:
            break

    return selected


def search_attractions(
    city: str,
    interests: list[str],
    limit: int = 12,
) -> list[Attraction]:
    if not 1 <= limit <= 30:
        raise ValueError("景点数量必须在1到30之间")

    location = get_coordinates(city)
    latitude = location["latitude"]
    longitude = location["longitude"]
    query = f"""
[out:json][timeout:15];
nwr(around:5000,{latitude},{longitude})
  ["tourism"~"attraction|museum|gallery|viewpoint|zoo|theme_park"]["name"];
out center 40;
"""
    elements = fetch_overpass_elements(query)

    attractions = parse_attractions(
        elements,
        latitude,
        longitude,
        interests,
        limit,
    )

    if not attractions:
        raise RuntimeError(f"没有检索到{city}的景点")

    return attractions
