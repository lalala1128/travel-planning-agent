import json
import logging

from openai.types.chat import ChatCompletionMessageParam

from ..schemas import SearchQueryAnalysis
from .llm import create_llm_client, get_llm_model


logger = logging.getLogger(__name__)
def normalize_city(city: str) -> str:
    normalized_city = city.strip()

    if normalized_city.endswith("市"):
        return normalized_city[:-1]

    return normalized_city


def has_city_conflict(
    requested_city: str | None,
    detected_city: str | None,
) -> bool:
    if not requested_city or not detected_city:
        return False

    return normalize_city(requested_city) != normalize_city(detected_city)
def rewrite_search_query(
    query: str,
    requested_city: str | None = None,
) -> SearchQueryAnalysis:
    client = create_llm_client()
    model = get_llm_model()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是RAG检索查询改写器，只输出JSON对象。"
                    "rewritten_query必须是适合向量检索的简洁关键词，"
                    "保留原问题中的专有名词、数字和核心意图。"
                    "detected_city只填写原问题明确提到的城市；"
                    "原问题没有城市时必须为null。"
                    "不要回答用户的问题。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        response_format={"type": "json_object"},
        reasoning_effort="none",
        temperature=0.0,
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("查询改写模型返回了空内容")

    data = json.loads(content)

    rewritten_query = str(
        data.get("rewritten_query") or query
    ).strip()

    detected_city = data.get("detected_city")

    if detected_city:
        detected_city = str(detected_city).strip()
    else:
        detected_city = None

    if not detected_city and requested_city:
        normalized_requested_city = normalize_city(
            requested_city
        )

        if normalized_requested_city in query:
            detected_city = normalized_requested_city

    city_conflict = has_city_conflict(
        requested_city,
        detected_city,
    )

    if requested_city and not city_conflict:
        normalized_city = normalize_city(requested_city)

        if normalized_city not in rewritten_query:
            rewritten_query = (
                f"{normalized_city} {rewritten_query}"
            )

    return SearchQueryAnalysis(
        original_query=query,
        rewritten_query=rewritten_query,
        detected_city=detected_city,
        city_conflict=city_conflict,
    )

def build_fallback_query_analysis(
    query: str,
    requested_city: str | None = None,
) -> SearchQueryAnalysis:
    rewritten_query = query.strip()

    if requested_city:
        normalized_city = normalize_city(requested_city)

        if normalized_city not in rewritten_query:
            rewritten_query = (
                f"{normalized_city} {rewritten_query}"
            )

    return SearchQueryAnalysis(
        original_query=query,
        rewritten_query=rewritten_query,
        detected_city=None,
        city_conflict=False,
    )
def rewrite_search_query_safely(
    query: str,
    requested_city: str | None = None,
) -> SearchQueryAnalysis:
    try:
        return rewrite_search_query(
            query=query,
            requested_city=requested_city,
        )
    except Exception:
        logger.exception(
            "查询改写失败，使用降级查询"
        )
        return build_fallback_query_analysis(
            query=query,
            requested_city=requested_city,
        )