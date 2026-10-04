

from ..models import Trip
from ..schemas import (
    Attraction,
    GeneratedPlan,
    KnowledgeSearchResult,
    PlanSource,
    RouteStop,
)
from .llm import create_llm_client, get_llm_model




def build_trip_prompt(
    trip: Trip,
    weather: dict,
    attractions: list[Attraction],
    route: list[RouteStop],
    knowledge: list[KnowledgeSearchResult],
    correction: str | None = None,
) -> str:
    interests = (
        "、".join(trip.interests)
        if trip.interests
        else "城市观光"
    )

    correction_text = ""

    if correction:
        correction_text = f"""
上一次生成的计划没有通过校验，错误如下：
{correction}
请修正这些错误后重新生成完整计划。
"""

    weather_text = "\n".join(
        (
            f"- {item['date']}：{item['weather']}，"
            f"{item['temperature_min']}~"
            f"{item['temperature_max']}摄氏度，"
            f"降水概率{item['precipitation_probability']}%"
        )
        for item in weather["forecasts"]
    )
    attraction_text = "\n".join(
        (
            f"- {item.name}（{item.category}，"
            f"距市中心约{item.distance_from_center_km}公里）"
        )
        for item in attractions
    )
    route_text = "\n".join(
        (
            f"- 第{item.day}天第{item.order}站：{item.name}，"
            f"与上一站距离{item.distance_from_previous_km}公里，"
            f"建议{item.suggested_transport}，"
            f"约{item.estimated_minutes}分钟"
        )
        for item in route
    )
    knowledge_text = "\n".join(
        (
            f"[{index}] {item.title}：{item.text}\n"
            f"来源：{item.source}"
        )
        for index, item in enumerate(knowledge, start=1)
    ) or "当前知识库没有该城市的补充资料。"

    return f"""
请为用户生成一份结合真实天气的旅行计划。

目的地：{trip.destination}
开始日期：{trip.start_date.isoformat()}
旅行天数：{trip.days}天
总预算：{trip.budget}元
兴趣偏好：{interests}

天气工具结果：
{weather_text}

景点检索结果：
{attraction_text}

路线估算结果：
{route_text}

知识库检索结果：
{knowledge_text}

要求：
1. 必须参考天气工具返回的数据。
2. 雨天优先安排室内活动。
3. 高温天气避免中午长时间户外活动。
4. 必须生成恰好{trip.days}天的计划。
5. 总预计费用不能超过{trip.budget}元。
6. 每天至少安排3项活动。
7. 只返回合法JSON，不要返回Markdown。
8. 优先使用景点检索结果，不要编造不存在的地点。
9. 安排顺序应参考路线估算结果，减少折返。
10. 如使用知识库信息，在概要中用[1]、[2]形式标明来源编号。
11. 每项活动必须根据路线估算结果填写与上一站的距离和预计交通时间。
{correction_text}

JSON结构必须为：
{{
  "destination": "目的地",
  "summary": "计划概要",
  "total_estimated_cost": 0,
  "days": [
    {{
      "day": 1,
      "date": "YYYY-MM-DD",
      "weather": "当天真实天气摘要",
      "theme": "当天主题",
      "daily_estimated_cost": 0,
      "activities": [
        {{
          "time": "09:00",
          "place": "地点",
          "activity": "活动内容",
          "transport": "交通方式",
          "distance_from_previous_km": 0,
          "estimated_travel_minutes": 0,
          "estimated_cost": 0
        }}
      ]
    }}
  ]
}}
"""


def generate_plan(
    trip: Trip,
    weather: dict,
    attractions: list[Attraction],
    route: list[RouteStop],
    knowledge: list[KnowledgeSearchResult],
    correction: str | None = None,
) -> GeneratedPlan:
    client = create_llm_client()
    model = get_llm_model()

    messages = [
        {
            "role": "system",
            "content": (
                "你是旅行规划Agent。必须严格依据提供的天气、"
                "景点与路线工具结果生成可执行的旅行计划。"
            ),
        },
        {
            "role": "user",
            "content": build_trip_prompt(
                trip,
                weather,
                attractions,
                route,
                knowledge,
                correction,
            ),
        },
    ]

    final_response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        reasoning_effort="none",
        temperature=0.4,
    )

    content = final_response.choices[0].message.content

    if not content:
        raise RuntimeError("模型没有返回最终旅行计划")

    plan = GeneratedPlan.model_validate_json(content)
    plan.sources = [
        PlanSource(
            title=item.title,
            source=item.source,
            excerpt=item.text[:200],
        )
        for item in knowledge
    ]
    return plan
