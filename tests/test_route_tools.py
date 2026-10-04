from app.schemas import Attraction
from app.tools import places as places_module
from app.tools.places import (
    haversine_km,
    parse_attractions,
    search_attractions,
)
from app.tools.routes import build_route, transport_for_distance


def test_haversine_distance_is_reasonable():
    distance = haversine_km(30.5728, 104.0668, 30.6586, 104.0648)
    assert 9 < distance < 11


def test_parse_attractions_removes_duplicates_and_ranks_interest():
    elements = [
        {
            "lat": 30.57,
            "lon": 104.06,
            "tags": {"name": "普通景点", "tourism": "attraction"},
        },
        {
            "lat": 30.58,
            "lon": 104.07,
            "tags": {"name": "历史博物馆", "historic": "museum"},
        },
        {
            "lat": 30.59,
            "lon": 104.08,
            "tags": {"name": "历史博物馆", "historic": "museum"},
        },
    ]

    attractions = parse_attractions(
        elements,
        center_latitude=30.57,
        center_longitude=104.06,
        interests=["历史"],
        limit=5,
    )

    assert len(attractions) == 2
    assert attractions[0].name == "历史博物馆"


def test_parse_attractions_limits_restaurant_ratio():
    elements = [
        {
            "lat": 30.57 + index * 0.001,
            "lon": 104.06,
            "tags": {
                "name": f"餐馆{index}",
                "amenity": "restaurant",
            },
        }
        for index in range(6)
    ]
    elements.extend(
        {
            "lat": 30.58 + index * 0.001,
            "lon": 104.07,
            "tags": {
                "name": f"景点{index}",
                "tourism": "attraction",
            },
        }
        for index in range(6)
    )

    attractions = parse_attractions(
        elements,
        center_latitude=30.57,
        center_longitude=104.06,
        interests=["美食"],
        limit=6,
    )

    restaurant_count = sum(
        item.category == "restaurant" for item in attractions
    )
    assert len(attractions) == 6
    assert restaurant_count == 2


def test_search_attractions_uses_bounded_core_query(
    monkeypatch,
):
    queries = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "elements": [
                    {
                        "lat": 28.20 + index * 0.001,
                        "lon": 112.97,
                        "tags": {
                            "name": f"核心景点{index}",
                            "tourism": "attraction",
                        },
                    }
                    for index in range(3)
                ]
            }

    def fake_post(url, data, headers, timeout):
        queries.append(data["data"])
        return FakeResponse()

    monkeypatch.setattr(
        places_module,
        "get_coordinates",
        lambda city: {
            "latitude": 28.19874,
            "longitude": 112.97087,
        },
    )
    monkeypatch.setattr(places_module.httpx, "post", fake_post)

    attractions = search_attractions("长沙", ["美食"], limit=3)

    assert [item.name for item in attractions] == [
        "核心景点0",
        "核心景点1",
        "核心景点2",
    ]
    assert len(queries) == 1
    assert "around:5000" in queries[0]
    assert '"amenity"="restaurant"' not in queries[0]


def test_build_route_assigns_stops_to_days():
    attractions = [
        Attraction(
            name=f"景点{index}",
            category="attraction",
            latitude=30.57 + index * 0.01,
            longitude=104.06,
            distance_from_center_km=float(index),
        )
        for index in range(6)
    ]

    route = build_route(attractions, days=2, stops_per_day=3)

    assert len(route) == 6
    assert [stop.day for stop in route] == [1, 1, 1, 2, 2, 2]
    assert route[0].suggested_transport == "当天首站"
    assert route[3].suggested_transport == "当天首站"


def test_transport_changes_with_distance():
    assert transport_for_distance(1)[0] == "步行"
    assert transport_for_distance(5)[0] == "公共交通"
    assert transport_for_distance(12)[0] == "出租车/网约车"
