from app.schemas import KnowledgeSearchResult
from app.services.rag_answer import select_cited_results


def test_select_cited_results_keeps_only_used_source():
    results = [
        KnowledgeSearchResult(
            text="长沙预算每天260元。",
            source="changsha.md",
            title="长沙攻略",
            city="长沙",
            chunk_index=12,
            vector_score=0.6,
            keyword_score=0.8,
            final_score=0.64,
        ),
        KnowledgeSearchResult(
            text="长沙适合安排三日游。",
            source="changsha.md",
            title="长沙攻略",
            city="长沙",
            chunk_index=2,
            vector_score=0.5,
            keyword_score=0.2,
            final_score=0.44,
        ),
    ]

    answer = (
        "测试预算为每天260元"
        "[来源: changsha.md#12]。"
    )

    cited_results = select_cited_results(
        answer=answer,
        results=results,
    )

    assert len(cited_results) == 1
    assert cited_results[0].chunk_index == 12