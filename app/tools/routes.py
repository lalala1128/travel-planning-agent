from ..schemas import Attraction, RouteStop
from .places import haversine_km


def transport_for_distance(distance_km: float) -> tuple[str, int]:
    if distance_km <= 1.5:
        return "步行", max(5, round(distance_km * 15))
    if distance_km <= 8:
        return "公共交通", max(15, round(distance_km * 6 + 10))
    return "出租车/网约车", max(20, round(distance_km * 3 + 10))


def build_route(
    attractions: list[Attraction],
    days: int,
    stops_per_day: int = 3,
) -> list[RouteStop]:
    if days < 1:
        raise ValueError("旅行天数必须大于0")
    if not attractions:
        raise ValueError("至少需要一个景点才能规划路线")

    remaining = attractions.copy()
    ordered = [remaining.pop(0)]

    while remaining:
        previous = ordered[-1]
        nearest = min(
            remaining,
            key=lambda attraction: haversine_km(
                previous.latitude,
                previous.longitude,
                attraction.latitude,
                attraction.longitude,
            ),
        )
        ordered.append(nearest)
        remaining.remove(nearest)

    selected = ordered[: days * stops_per_day]
    route = []

    for index, attraction in enumerate(selected):
        day = index // stops_per_day + 1
        order = index % stops_per_day + 1
        distance = 0.0

        if order > 1:
            previous = selected[index - 1]
            distance = haversine_km(
                previous.latitude,
                previous.longitude,
                attraction.latitude,
                attraction.longitude,
            )

        transport, minutes = transport_for_distance(distance)
        route.append(
            RouteStop(
                day=day,
                order=order,
                name=attraction.name,
                latitude=attraction.latitude,
                longitude=attraction.longitude,
                distance_from_previous_km=round(distance, 2),
                suggested_transport=(
                    "当天首站" if order == 1 else transport
                ),
                estimated_minutes=0 if order == 1 else minutes,
            )
        )

    return route
