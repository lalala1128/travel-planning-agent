import json

from openai.types.chat import ChatCompletionMessageParam

from ..schemas import KnowledgeSearchResult
from .llm import create_llm_client, get_llm_model


def build_knowledge_context(
    results: list[KnowledgeSearchResult],
) -> str:
    return "\n\n".join(
        (
            f"[来源: {result.source}"
            f"#{result.chunk_index}]\n"
            f"{result.text}"
        )
        for result in results
    )

def select_cited_results(
    answer: str,
    results: list[KnowledgeSearchResult],
) -> list[KnowledgeSearchResult]:
    cited_results = []

    for result in results:
        citation_marker = (
            f"[来源: {result.source}"
            f"#{result.chunk_index}]"
        )

        if citation_marker in answer:
            cited_results.append(result)

    return cited_results
def generate_knowledge_answer(
    query: str,
    results: list[KnowledgeSearchResult],
) -> str:
    if not results:
        return "知识库中没有足够资料回答这个问题。"

    context = build_knowledge_context(results)
    client = create_llm_client()
    model = get_llm_model()

    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": (
                "你是旅游知识库问答助手。"
                "只能依据提供的知识片段回答，禁止补充片段中"
                "没有的信息。如果资料不足，明确说明无法确定。"
                "回答中的事实必须使用给定的来源标记，例如"
                "[来源: document.md#3]。只输出JSON对象，"
                "格式为{\"answer\": \"回答内容\"}。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{query}\n\n"
                f"知识片段：\n{context}"
            ),
        },
    ]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        reasoning_effort="none",
        temperature=0.1,
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("知识库问答模型返回了空内容")

    data = json.loads(content)
    answer = str(data.get("answer") or "").strip()

    if not answer:
        raise RuntimeError("知识库问答结果缺少 answer")

    return answer