from app.schemas import KnowledgeSearchResult
from scripts import evaluate_rag


def test_evaluate_records_missed_positive_and_negative_rejection(monkeypatch):
    cases = [
        {
            "query": "found",
            "city": "test",
            "should_match": True,
            "expected_terms": ["expected"],
        },
        {
            "query": "missed",
            "city": "test",
            "should_match": True,
            "expected_terms": ["expected"],
        },
        {
            "query": "negative",
            "city": "test",
            "should_match": False,
            "expected_terms": [],
        },
    ]

    class FakeRagService:
        def search(self, tenant_id, query, city, limit):
            if query == "negative":
                return []
            return [
                KnowledgeSearchResult(
                    text="expected" if query == "found" else "unrelated",
                    source="test.md",
                    title="Test",
                    city=city,
                    chunk_index=0,
                    vector_score=0.8,
                    keyword_score=0.0,
                    final_score=0.8,
                )
            ]

    monkeypatch.setattr(evaluate_rag, "load_cases", lambda: cases)
    monkeypatch.setattr(
        evaluate_rag, "get_rag_service", lambda: FakeRagService()
    )

    report = evaluate_rag.evaluate(tenant_id=1)

    assert report["total_cases"] == 3
    assert report["hit_at_k"] == 0.5
    assert report["mrr"] == 0.5
    assert report["negative_rejection_rate"] == 1.0
    assert report["cases"][1]["passed"] is False
    assert report["cases"][1]["relevant_rank"] is None


def test_evaluate_records_empty_positive_results(monkeypatch):
    monkeypatch.setattr(
        evaluate_rag,
        "load_cases",
        lambda: [{
            "query": "missing",
            "city": "test",
            "should_match": True,
            "expected_terms": ["expected"],
        }],
    )

    class EmptyRagService:
        def search(self, tenant_id, query, city, limit):
            return []

    monkeypatch.setattr(
        evaluate_rag, "get_rag_service", lambda: EmptyRagService()
    )

    report = evaluate_rag.evaluate(tenant_id=1)

    assert report["hit_at_k"] == 0.0
    assert report["mrr"] == 0.0
    assert report["cases"][0]["returned_count"] == 0
