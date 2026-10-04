import os
import re
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastembed import TextEmbedding
from pypdf import PdfReader
from qdrant_client import QdrantClient, models

from ..schemas import KnowledgeSearchResult


COLLECTION_NAME = "travel_knowledge"
MIN_FINAL_SCORE = 0.15
DEFAULT_MODEL = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)


def extract_document_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()

    if suffix in {".txt", ".md"}:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gb18030")

    if suffix == ".pdf":
        reader = PdfReader(BytesIO(content))
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )

    raise ValueError("仅支持 .txt、.md 和 .pdf 文件")


def split_document(
    text: str,
    chunk_size: int = 500,
    overlap: int = 80,
) -> list[str]:
    if chunk_size < 100:
        raise ValueError("chunk_size 不能小于100")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap 必须小于 chunk_size")

    normalized = re.sub(r"\r\n?", "\n", text)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()

    if not normalized:
        return []

    chunks = []
    start = 0

    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))

        if end < len(normalized):
            candidates = [
                normalized.rfind(separator, start, end)
                for separator in ("\n\n", "。", "！", "？", "；")
            ]
            boundary = max(candidates)
            if boundary > start + chunk_size // 2:
                end = boundary + 1

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)

    return chunks
def split_markdown_document(
    text: str,
    chunk_size: int = 500,
    overlap: int = 80,
) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", text).strip()

    sections = re.split(
        r"(?=^#{1,6}\s+)",
        normalized,
        flags=re.MULTILINE,
    )

    chunks = []

    for section in sections:
        section = section.strip()

        if not section:
            continue
        if re.fullmatch(
                r"#{1,6}\s+[^\n]+",
                section,
        ):
            continue
        chunks.extend(
            split_document(
                section,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        )

    return chunks

def character_ngrams(text: str, size: int = 2) -> set[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {
        normalized[index:index + size]
        for index in range(len(normalized) - size + 1)
    }


def rerank_results(
    query: str,
    results: list[KnowledgeSearchResult],
) -> list[KnowledgeSearchResult]:
    query_terms = character_ngrams(query)
    reranked_results = []

    for result in results:
        document_terms = character_ngrams(result.text)

        keyword_score = (
            len(query_terms & document_terms) / len(query_terms)
            if query_terms
            else 0.0
        )

        final_score = (
            result.vector_score * 0.8
            + keyword_score * 0.2
        )

        reranked_result = result.model_copy(
            update={
                "keyword_score": keyword_score,
                "final_score": final_score,
            }
        )
        reranked_results.append(reranked_result)

    return sorted(
        reranked_results,
        key=lambda item: item.final_score,
        reverse=True,
    )
def filter_results_by_score(
    results: list[KnowledgeSearchResult],
    min_score: float = MIN_FINAL_SCORE,
) -> list[KnowledgeSearchResult]:
    return [
        result
        for result in results
        if result.final_score >= min_score
    ]

class RagService:
    def __init__(self) -> None:
        self.model_name = os.getenv(
            "EMBEDDING_MODEL",
            DEFAULT_MODEL,
        )
        self.cache_dir = os.getenv(
            "EMBEDDING_CACHE_DIR",
            "data/models",
        )
        qdrant_url = os.getenv("QDRANT_URL")

        if qdrant_url:
            self.client = QdrantClient(url=qdrant_url)
        else:
            qdrant_path = os.getenv(
                "QDRANT_PATH",
                "data/qdrant",
            )
            Path(qdrant_path).mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=qdrant_path)

        self._embedding: TextEmbedding | None = None

    @property
    def embedding(self) -> TextEmbedding:
        if self._embedding is None:
            self._embedding = TextEmbedding(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
            )
        return self._embedding

    def ensure_collection(self) -> None:
        if self.client.collection_exists(COLLECTION_NAME):
            return

        self.client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=self.embedding.embedding_size,
                distance=models.Distance.COSINE,
            ),
        )

    def ingest(
        self,
        filename: str,
        content: bytes,
        city: str | None = None,
    ) -> int:
        text = extract_document_text(filename, content)
        suffix = Path(filename).suffix.lower()

        if suffix == ".md":
            chunks = split_markdown_document(text)
        else:
            chunks = split_document(text)

        if not chunks:
            raise ValueError("文档中没有可入库的文本")

        self.ensure_collection()
        vectors = list(self.embedding.embed(chunks))
        title = Path(filename).stem
        points = []

        for index, (chunk, vector) in enumerate(
            zip(chunks, vectors, strict=True)
        ):
            points.append(
                models.PointStruct(
                    id=str(uuid4()),
                    vector=vector.tolist(),
                    payload={
                        "text": chunk,
                        "source": filename,
                        "title": title,
                        "city": city,
                        "chunk_index": index,
                    },
                )
            )
        self.client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="source",
                        match=models.MatchValue(
                            value=filename,
                        ),
                    )
                ]
            ),
            wait=True,
        )
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True,
        )
        return len(points)

    def search(
        self,
        query: str,
        city: str | None = None,
        limit: int = 5,
    ) -> list[KnowledgeSearchResult]:
        if not self.client.collection_exists(COLLECTION_NAME):
            return []

        query_vector = next(self.embedding.query_embed(query)).tolist()
        query_filter = None

        if city:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="city",
                        match=models.MatchValue(value=city),
                    )
                ]
            )

        response = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=query_filter,
            limit=min(20, max(limit * 3, limit)),
            with_payload=True,
        )
        results = []

        for point in response.points:
            payload = point.payload or {}
            results.append(
                KnowledgeSearchResult(
                    text=str(payload.get("text", "")),
                    source=str(payload.get("source", "")),
                    title=str(payload.get("title", "")),
                    city=payload.get("city"),
                    chunk_index=int(payload.get("chunk_index", 0)),
                    vector_score=float(point.score),
                    keyword_score=0.0,
                    final_score=float(point.score),
                )
            )

        reranked_results = rerank_results(query, results)

        filtered_results = filter_results_by_score(reranked_results)

        return filtered_results[:limit]

_service: RagService | None = None
_service_lock = Lock()


def get_rag_service() -> RagService:
    global _service

    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RagService()

    return _service
