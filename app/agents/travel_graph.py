from datetime import timedelta
from math import isclose
import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..models import Trip
from ..schemas import (
    Attraction,
    GeneratedPlan,
    KnowledgeSearchResult,
    RouteStop,
)
from ..services.cache import cache
from ..services.planner import generate_plan
from ..services.rag import get_rag_service
from ..tools.places import search_attractions
from ..tools.routes import build_route
from ..tools.weather import get_weather

MAX_DAILY_TRAVEL_MINUTES = 240
class TravelState(TypedDict):
    trip: Trip
    weather: dict | None
    attractions: list[Attraction]
    route: list[RouteStop]
    knowledge: list[KnowledgeSearchResult]
    plan: GeneratedPlan | None
    error: str | None
    attempts: int


def weather_node(state: TravelState) -> dict:
    trip = state["trip"]
    cache_key = (
        f"travel:weather:{trip.destination}:"
        f"{trip.start_date.isoformat()}:{trip.days}"
    )

    try:
        weather = cache.get_json(cache_key)
        if weather is None:
            weather = get_weather(
                city=trip.destination,
                start_date=trip.start_date.isoformat(),
                days=trip.days,
            )
            cache.set_json(cache_key, weather, ttl=30 * 60)

        return {
            "weather": weather,
            "error": None,
        }
    except Exception as exc:
        return {"weather": None, "error": str(exc)}


def attraction_node(state: TravelState) -> dict:
    trip = state["trip"]
    limit = min(30, max(9, trip.days * 4))
    interests = ",".join(sorted(trip.interests))
    cache_key = (
        f"travel:attractions:{trip.destination}:"
        f"{interests}:{limit}"
    )

    try:
        cached = cache.get_json(cache_key)

        if cached is None:
            attractions = search_attractions(
                city=trip.destination,
                interests=trip.interests,
                limit=limit,
            )
            cache.set_json(
                cache_key,
                [item.model_dump() for item in attractions],
                ttl=6 * 60 * 60,
            )
        else:
            attractions = [
                Attraction.model_validate(item) for item in cached
            ]

        return {"attractions": attractions, "error": None}
    except Exception as exc:
        return {"attractions": [], "error": str(exc)}


def route_node(state: TravelState) -> dict:
    try:
        route = build_route(
            state["attractions"],
            state["trip"].days,
        )
        return {"route": route, "error": None}
    except Exception as exc:
        return {"route": [], "error": str(exc)}


def knowledge_node(state: TravelState) -> dict:
    trip = state["trip"]
    query = " ".join(
        [trip.destination, *trip.interests, "旅行 景点 注意事项"]
    )

    try:
        results = get_rag_service().search(
            tenant_id=trip.tenant_id,
            query=query,
            city=trip.destination,
            limit=5,
        )
        return {"knowledge": results, "error": None}
    except Exception as exc:
        return {"knowledge": [], "error": str(exc)}


def generate_plan_node(
    state: TravelState,
) -> dict:
    attempts = state["attempts"] + 1

    try:
        plan = generate_plan(
            state["trip"],
            weather=state["weather"],
            attractions=state["attractions"],
            route=state["route"],
            knowledge=state["knowledge"],
            correction=state["error"],
        )
        normalize_plan_costs(plan)

        return {
            "plan": plan,
            "error": None,
            "attempts": attempts,
        }

    except Exception as exc:
        return {
            "plan": None,
            "error": str(exc),
            "attempts": attempts,
        }


def normalize_plan_costs(plan: GeneratedPlan) -> None:
    for day_plan in plan.days:
        day_plan.daily_estimated_cost = round(
            sum(
                activity.estimated_cost
                for activity in day_plan.activities
            ),
            2,
        )

    plan.total_estimated_cost = round(
        sum(day.daily_estimated_cost for day in plan.days),
        2,
    )
    summary_parts = re.split(r"(?<=[。！？])", plan.summary)
    summary_without_cost = "".join(
        part for part in summary_parts
        if "费用" not in part and "预算" not in part
    ).strip()
    plan.summary = (
        f"{summary_without_cost} "
        f"按活动明细核算，预计总费用为"
        f"{plan.total_estimated_cost:g}元。"
    ).strip()

def activity_time_to_minutes(value: str) -> int | None:
    match = re.fullmatch(
        r"([01]\d|2[0-3]):([0-5]\d)",
        value,
    )
    if match is None:
        return None

    hour, minute = match.groups()
    return int(hour) * 60 + int(minute)
def collect_plan_errors(
    trip: Trip,
    plan: GeneratedPlan,
    weather: dict | None = None,
    route: list[RouteStop] | None = None,
) -> list[str]:
    errors = []

    forecast_by_date = {
        str(item["date"]): str(item["weather"]).strip()
        for item in (weather or {}).get("forecasts", [])
        if item.get("date") and item.get("weather")
    }
    route_by_day_and_name = {
        (stop.day, stop.name): stop
        for stop in route or []
    }

    if len(plan.days) != trip.days:
        errors.append(
            f"计划天数应为{trip.days}天，"
            f"实际为{len(plan.days)}天"
        )

    if plan.total_estimated_cost > trip.budget:
        errors.append(
            f"预计费用{plan.total_estimated_cost}元"
            f"超过预算{trip.budget}元"
        )

    expected_daily_total = sum(
        day.daily_estimated_cost for day in plan.days
    )
    if not isclose(
        plan.total_estimated_cost,
        expected_daily_total,
        rel_tol=0.01,
        abs_tol=1.0,
    ):
        errors.append("总费用与每日费用合计不一致")

    for index, day_plan in enumerate(plan.days):
        expected_day = index + 1
        expected_date = trip.start_date + timedelta(days=index)

        if day_plan.day != expected_day:
            errors.append(
                f"第{expected_day}天的day字段不正确"
            )
        if day_plan.date != expected_date:
            errors.append(
                f"第{expected_day}天日期应为{expected_date}"
            )
        expected_weather = forecast_by_date.get(
            day_plan.date.isoformat()
        )

        if (
            expected_weather
            and expected_weather not in day_plan.weather
        ):
            errors.append(
                f"第{expected_day}天天气与实时预报不一致："
                f"预报为{expected_weather}，"
                f"计划中为{day_plan.weather}"
            )
        if len(day_plan.activities) < 3:
            errors.append(
                f"第{expected_day}天至少需要3项活动"
            )

        activity_minutes = []

        for activity in day_plan.activities:
            minutes = activity_time_to_minutes(activity.time)

            if minutes is None:
                errors.append(
                    f"第{expected_day}天活动时间格式应为HH:MM"
                )
            else:
                activity_minutes.append(minutes)

            route_stop = route_by_day_and_name.get(
                (expected_day, activity.place)
            )

            if route_stop is not None:
                distance_matches = isclose(
                    activity.distance_from_previous_km,
                    route_stop.distance_from_previous_km,
                    rel_tol=0.05,
                    abs_tol=0.2,
                )
                travel_time_matches = (
                        abs(
                            activity.estimated_travel_minutes
                            - route_stop.estimated_minutes
                        )
                        <= 5
                )

                if not distance_matches or not travel_time_matches:
                    errors.append(
                        f"第{expected_day}天{activity.place}的"
                        f"距离或交通时间与路线工具结果不一致"
                    )

        times_are_ordered = all(
            current > previous
            for previous, current in zip(
                activity_minutes,
                activity_minutes[1:],
            )
        )

        if (
                len(activity_minutes) == len(day_plan.activities)
                and not times_are_ordered
        ):
            errors.append(
                f"第{expected_day}天活动时间必须按先后顺序排列"
            )
        daily_travel_minutes = sum(
            activity.estimated_travel_minutes
            for activity in day_plan.activities
        )

        if daily_travel_minutes > MAX_DAILY_TRAVEL_MINUTES:
            errors.append(
                f"第{expected_day}天单日交通时间不能超过"
                f"{MAX_DAILY_TRAVEL_MINUTES}分钟，"
                f"实际为{daily_travel_minutes}分钟"
            )

        activity_total = sum(
            activity.estimated_cost
            for activity in day_plan.activities
        )


        if not isclose(
            day_plan.daily_estimated_cost,
            activity_total,
            rel_tol=0.01,
            abs_tol=1.0,
        ):
            errors.append(
                f"第{expected_day}天费用与活动费用合计不一致"
            )

    return errors


def validate_plan_node(
    state: TravelState,
) -> dict:
    plan = state["plan"]

    if plan is None:
        return {}

    errors = collect_plan_errors(
        state["trip"],
        plan,
        weather=state["weather"],
        route=state["route"],
    )

    if errors:
        return {
            "plan": None,
            "error": "；".join(errors),
        }

    return {
        "error": None,
    }


def route_after_validation(
    state: TravelState,
) -> Literal["retry", "complete", "failed"]:
    if state["plan"] is not None and state["error"] is None:
        return "complete"

    if state["attempts"] < 2:
        return "retry"

    return "failed"


def route_after_tool(
    state: TravelState,
) -> Literal["continue", "failed"]:
    return "failed" if state["error"] else "continue"


builder = StateGraph(TravelState)

builder.add_node("weather", weather_node)
builder.add_node("attractions", attraction_node)
builder.add_node("route", route_node)
builder.add_node("knowledge", knowledge_node)

builder.add_node(
    "generate_plan",
    generate_plan_node,
)

builder.add_node(
    "validate_plan",
    validate_plan_node,
)

builder.add_edge(
    START,
    "weather",
)

builder.add_conditional_edges(
    "weather",
    route_after_tool,
    {"continue": "attractions", "failed": END},
)

builder.add_conditional_edges(
    "attractions",
    route_after_tool,
    {"continue": "route", "failed": END},
)

builder.add_conditional_edges(
    "route",
    route_after_tool,
    {"continue": "knowledge", "failed": END},
)

builder.add_conditional_edges(
    "knowledge",
    route_after_tool,
    {"continue": "generate_plan", "failed": END},
)

builder.add_edge("generate_plan", "validate_plan")

builder.add_conditional_edges(
    "validate_plan",
    route_after_validation,
    {
        "retry": "generate_plan",
        "complete": END,
        "failed": END,
    },
)

travel_graph = builder.compile()


def initial_state(trip: Trip) -> TravelState:
    return {
        "trip": trip,
        "weather": None,
        "attractions": [],
        "route": [],
        "knowledge": [],
        "plan": None,
        "error": None,
        "attempts": 0,
    }


def run_travel_graph(trip: Trip) -> GeneratedPlan:
    result = travel_graph.invoke(initial_state(trip))

    plan = result["plan"]

    if plan is None:
        raise RuntimeError(
            "LangGraph 生成失败："
            f"{result['error']}"
        )

    return plan


NODE_MESSAGES = {
    "weather": "天气查询完成",
    "attractions": "景点检索完成",
    "route": "路线估算完成",
    "knowledge": "旅游知识检索完成",
    "generate_plan": "旅行计划生成完成",
    "validate_plan": "计划约束校验完成",
}


def stream_travel_graph(trip: Trip):
    state = initial_state(trip)

    for update in travel_graph.stream(
        state,
        stream_mode="updates",
    ):
        for node, values in update.items():
            state.update(values)
            yield {
                "node": node,
                "message": NODE_MESSAGES.get(node, node),
                "attempts": state["attempts"],
                "error": state["error"],
            }

    plan = state["plan"]
    if plan is None:
        raise RuntimeError(
            "LangGraph 生成失败："
            f"{state['error']}"
        )

    yield {
        "node": "complete",
        "message": "旅行计划生成成功",
        "attempts": state["attempts"],
        "error": None,
        "plan": plan,
    }
