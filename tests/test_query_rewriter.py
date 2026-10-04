import app.services.query_rewriter as query_rewriter
from app.services.query_rewriter import has_city_conflict


def test_same_city_with_suffix_does_not_conflict():
    assert has_city_conflict("长沙", "长沙市") is False


def test_different_cities_conflict():
    assert has_city_conflict("长沙", "北京") is True


def test_missing_detected_city_does_not_conflict():
    assert has_city_conflict("长沙", None) is False

def test_safe_rewrite_falls_back_when_llm_fails(
        monkeypatch,
):
    def raise_model_error(*args, **kwargs):
        raise RuntimeError("模拟DeepSeek连接失败")

    monkeypatch.setattr(
        query_rewriter,
        "rewrite_search_query",
        raise_model_error,
    )

    result = query_rewriter.rewrite_search_query_safely(
        query="三天怎么玩",
        requested_city="长沙",
    )

    assert result.original_query == "三天怎么玩"
    assert result.rewritten_query == "长沙 三天怎么玩"
    assert result.detected_city is None
    assert result.city_conflict is False