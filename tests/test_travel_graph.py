
from datetime import date

import app.agents.travel_graph as graph_module
from app.agents.travel_graph import (
    collect_plan_errors,
    normalize_plan_costs,
    run_travel_graph,
)
from app.models import Trip
from app.schemas import (
    Attraction,
    GeneratedPlan,
    KnowledgeSearchResult,
    RouteStop,
)


def build_trip() -> Trip:
    return Trip(
        destination="成都",
        start_date=date(2026, 10, 1),
        days=1,
        budget=1000,
        interests=["美食"],
    )


def build_plan() -> GeneratedPlan:
    return GeneratedPlan.model_validate(
        {
            "destination": "成都",
            "summary": "一天成都旅行",
            "total_estimated_cost": 300,
            "days": [
                {
                    "day": 1,
                    "date": "2026-10-01",
                    "weather": "晴",
                    "theme": "城市漫步",
                    "daily_estimated_cost": 300,
                    "activities": [
                        {
                            "time": "09:00",
                            "place": "人民公园",
                            "activity": "游览",
                            "transport": "地铁",
                            "distance_from_previous_km": 0,
                            "estimated_travel_minutes": 0,
                            "estimated_cost": 50,
                        },
                        {
                            "time": "12:00",
                            "place": "奎星楼街",
                            "activity": "午餐",
                            "transport": "步行",
                            "distance_from_previous_km": 2.5,
                            "estimated_travel_minutes": 25,
                            "estimated_cost": 100,
                        },
                        {
                            "time": "15:00",
                            "place": "武侯祠",
                            "activity": "参观",
                            "transport": "公交",
                            "distance_from_previous_km": 4,
                            "estimated_travel_minutes": 35,
                            "estimated_cost": 150,
                        },
                    ],
                }
            ],
        }
    )


def test_valid_plan_has_no_errors():
    assert collect_plan_errors(build_trip(), build_plan()) == []


def test_plan_validation_finds_budget_and_activity_errors():
    trip = build_trip()
    plan = build_plan()
    trip.budget = 200
    plan.days[0].activities.pop()

    errors = collect_plan_errors(trip, plan)

    assert any("超过预算" in error for error in errors)
    assert any("至少需要3项活动" in error for error in errors)
    assert any("活动费用合计不一致" in error for error in errors)

def test_plan_validation_finds_invalid_activity_time_order():
    trip = build_trip()
    plan = build_plan()

    plan.days[0].activities[0].time = "15:00"
    plan.days[0].activities[1].time = "09:00"
    plan.days[0].activities[2].time = "12:00"

    errors = collect_plan_errors(trip, plan)

    assert any("活动时间必须按先后顺序排列" in error for error in errors)
def test_plan_validation_finds_weather_mismatch():
    trip = build_trip()
    plan = build_plan()

    weather = {
        "forecasts": [
            {
                "date": "2026-10-01",
                "weather": "中雨",
                "temperature_min": 18,
                "temperature_max": 23,
                "precipitation_probability": 85,
            }
        ]
    }

    errors = collect_plan_errors(
        trip,
        plan,
        weather=weather,
    )

    assert any(
        "天气与实时预报不一致" in error
        for error in errors
    )


def test_plan_validation_finds_excessive_daily_travel_time():
    trip = build_trip()
    plan = build_plan()

    for activity in plan.days[0].activities:
        activity.estimated_travel_minutes = 100

    errors = collect_plan_errors(trip, plan)

    assert any(
        "单日交通时间不能超过240分钟" in error
        for error in errors
    )


def test_plan_validation_finds_route_data_mismatch():
    trip = build_trip()
    plan = build_plan()

    route = [
        RouteStop(
            day=1,
            order=1,
            name="人民公园",
            latitude=30.57,
            longitude=104.06,
            distance_from_previous_km=0,
            suggested_transport="当天首站",
            estimated_minutes=0,
        ),
        RouteStop(
            day=1,
            order=2,
            name="奎星楼街",
            latitude=30.58,
            longitude=104.07,
            distance_from_previous_km=2.5,
            suggested_transport="公共交通",
            estimated_minutes=25,
        ),
        RouteStop(
            day=1,
            order=3,
            name="武侯祠",
            latitude=30.64,
            longitude=104.04,
            distance_from_previous_km=4,
            suggested_transport="公共交通",
            estimated_minutes=35,
        ),
    ]

    plan.days[0].activities[1].distance_from_previous_km = 0.1
    plan.days[0].activities[1].estimated_travel_minutes = 1

    errors = collect_plan_errors(
        trip,
        plan,
        route=route,
    )

    assert any(
        "与路线工具结果不一致" in error
        for error in errors
    )


def test_normalize_plan_costs_recalculates_totals():
    plan = build_plan()
    plan.days[0].daily_estimated_cost = 999
    plan.total_estimated_cost = 888

    normalize_plan_costs(plan)

    assert plan.days[0].daily_estimated_cost == 300
    assert plan.total_estimated_cost == 300
    assert "预计总费用为300元" in plan.summary


def test_graph_retries_only_generation_node(monkeypatch):
    calls = {
        "weather": 0,
        "attractions": 0,
        "route": 0,
        "knowledge": 0,
        "generation": 0,
    }
    monkeypatch.setattr(
        graph_module.cache,
        "get_json",
        lambda key: None,
    )
    monkeypatch.setattr(
        graph_module.cache,
        "set_json",
        lambda key, value, ttl: None,
    )

    def fake_weather(**kwargs):
        calls["weather"] += 1
        return {
            "forecasts": [
                {
                    "date": "2026-10-01",
                    "weather": "晴",
                    "temperature_min": 20,
                    "temperature_max": 28,
                    "precipitation_probability": 0,
                }
            ]
        }

    def fake_attractions(**kwargs):
        calls["attractions"] += 1
        return [
            Attraction(
                name="人民公园",
                category="attraction",
                latitude=30.57,
                longitude=104.06,
                distance_from_center_km=1,
            )
        ]

    def fake_route(attractions, days):
        calls["route"] += 1
        return [
            RouteStop(
                day=1,
                order=1,
                name="人民公园",
                latitude=30.57,
                longitude=104.06,
                distance_from_previous_km=0,
                suggested_transport="当天首站",
                estimated_minutes=0,
            )
        ]

    class FakeRagService:
        def search(self, **kwargs):
            calls["knowledge"] += 1
            return [
                KnowledgeSearchResult(
                    text="人民公园适合体验成都茶馆文化。",
                    source="chengdu.md",
                    title="成都旅行指南",
                    city="成都",
                    chunk_index=0,
                    vector_score=0.9,
                    keyword_score=0.0,
                    final_score=0.9,
                )
            ]

    def fake_generation(
        trip,
        weather,
        attractions,
        route,
        knowledge,
        correction=None,
    ):
        calls["generation"] += 1
        plan = build_plan()

        if calls["generation"] == 1:
            for activity in plan.days[0].activities:
                activity.estimated_cost = 500
            assert correction is None
        else:
            assert "超过预算" in correction

        return plan

    monkeypatch.setattr(graph_module, "get_weather", fake_weather)
    monkeypatch.setattr(
        graph_module,
        "search_attractions",
        fake_attractions,
    )
    monkeypatch.setattr(graph_module, "build_route", fake_route)
    monkeypatch.setattr(
        graph_module,
        "get_rag_service",
        lambda: FakeRagService(),
    )
    monkeypatch.setattr(graph_module, "generate_plan", fake_generation)

    result = run_travel_graph(build_trip())

    assert result.total_estimated_cost == 300
    assert calls == {
        "weather": 1,
        "attractions": 1,
        "route": 1,
        "knowledge": 1,
        "generation": 2,
    }
