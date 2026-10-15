from app.schemas import KnowledgeSearchResult
from app.services.rag import (
    RagService,
    character_ngrams,
    extract_document_text,
    rerank_results,
    split_document,
    filter_results_by_score,
    split_markdown_document,
)


def test_extract_utf8_text_document():
    text = extract_document_text(
        "chengdu.md",
        "成都适合体验茶馆文化。".encode("utf-8"),
    )
    assert "茶馆文化" in text


def test_split_document_keeps_overlap():
    text = "成都旅行。" * 100
    chunks = split_document(text, chunk_size=120, overlap=20)

    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)
    assert all(len(chunk) <= 120 for chunk in chunks)


def test_character_ngrams_supports_chinese():
    assert "成都" in character_ngrams("成都旅行")
    assert "旅行" in character_ngrams("成都旅行")


def test_rerank_uses_query_text_overlap():
    results = [
        KnowledgeSearchResult(
            text="上海外滩夜景",
            source="shanghai.md",
            title="上海",
            chunk_index=0,
            vector_score=0.8,
            keyword_score=0.0,
            final_score=0.8,
        ),
        KnowledgeSearchResult(
            text="成都人民公园茶馆文化",
            source="chengdu.md",
            title="成都",
            chunk_index=0,
            vector_score=0.75,
            keyword_score=0.0,
            final_score=0.75,
        ),
    ]

    reranked = rerank_results("成都茶馆", results)

    assert reranked[0].source == "chengdu.md"

def test_filter_results_by_score_removes_low_scores():
    results = [
        KnowledgeSearchResult(
            text="Python虚拟环境",
            source="irrelevant.md",
            title="无关资料",
            chunk_index=0,
            vector_score=0.05,
            keyword_score=0.0,
            final_score=0.05,
        ),
        KnowledgeSearchResult(
            text="长沙三日旅行路线",
            source="changsha.md",
            title="长沙旅行",
            chunk_index=0,
            vector_score=0.30,
            keyword_score=0.0,
            final_score=0.30,
        ),
    ]

    filtered_results = filter_results_by_score(
        results,
        min_score=0.10,
    )

    assert len(filtered_results) == 1
    assert filtered_results[0].source == "changsha.md"
def test_split_markdown_document_keeps_sections_separate():
    text = (
        "# 长沙旅行攻略\n\n"
        "## 岳麓山\n"
        "岳麓山适合徒步并参观岳麓书院。\n\n"
        "## 橘子洲\n"
        "橘子洲适合沿湘江散步和观看夜景。"
    )

    chunks = split_markdown_document(
        text,
        chunk_size=120,
        overlap=20,
    )

    assert any("## 岳麓山" in chunk for chunk in chunks)
    assert any("## 橘子洲" in chunk for chunk in chunks)
    assert not any(
        "## 岳麓山" in chunk and "## 橘子洲" in chunk
        for chunk in chunks
    )


def test_search_always_filters_by_tenant():
    class Vector:
        def tolist(self):
            return [0.1, 0.2]

    class Embedding:
        def query_embed(self, query):
            yield Vector()

    class Client:
        def collection_exists(self, name):
            return True

        def query_points(self, **kwargs):
            self.query_filter = kwargs["query_filter"]
            return type("Response", (), {"points": []})()

    service = RagService.__new__(RagService)
    service.client = Client()
    service._embedding = Embedding()

    assert service.search(tenant_id=42, query="成都") == []
    condition = service.client.query_filter.must[0]
    assert condition.key == "tenant_id"
    assert condition.match.value == 42


def test_ingest_replaces_only_document_in_same_tenant():
    class Vector:
        def tolist(self):
            return [0.1, 0.2]

    class Embedding:
        embedding_size = 2

        def embed(self, chunks):
            for _ in chunks:
                yield Vector()

    class Client:
        def collection_exists(self, name):
            return True

        def delete(self, **kwargs):
            self.selector = kwargs["points_selector"]

        def upsert(self, **kwargs):
            self.points = kwargs["points"]

    service = RagService.__new__(RagService)
    service.client = Client()
    service._embedding = Embedding()

    assert service.ingest(tenant_id=42, filename="same.md", content=b"text") == 1
    conditions = {item.key: item.match.value for item in service.client.selector.must}
    assert conditions == {"tenant_id": 42, "source": "same.md"}
    assert service.client.points[0].payload["tenant_id"] == 42
