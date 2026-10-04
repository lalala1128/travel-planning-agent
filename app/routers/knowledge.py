from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)

from ..schemas import (
    KnowledgeAnswerResponse,
    KnowledgeAskRequest,
    KnowledgeIngestResponse,
    KnowledgeSearchResponse,
)
from ..services.query_rewriter import rewrite_search_query_safely
from ..services.rag import get_rag_service
from ..services.rag_answer import (
    generate_knowledge_answer,
    select_cited_results,
)

router = APIRouter(
    prefix="/knowledge",
    tags=["Knowledge"],
)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024


@router.post(
    "/documents",
    response_model=KnowledgeIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    file: Annotated[UploadFile, File()],
    city: Annotated[str | None, Form()] = None,
):
    filename = file.filename or "document.txt"
    content = file.file.read(MAX_UPLOAD_SIZE + 1)

    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="文档不能超过10MB",
        )

    try:
        chunks = get_rag_service().ingest(
            filename=filename,
            content=content,
            city=city,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"知识库入库失败：{exc}",
        ) from exc

    return {
        "source": filename,
        "city": city,
        "chunks": chunks,
    }


@router.get(
    "/search",
    response_model=KnowledgeSearchResponse,
)
def search_documents(
    query: str = Query(min_length=2, max_length=200),
    city: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=5, ge=1, le=10),
):
    try:
        analysis = rewrite_search_query_safely(
            query=query,
            requested_city=city,
        )

        if analysis.city_conflict:
            results = []
        else:
            results = get_rag_service().search(
                query=analysis.rewritten_query,
                city=city,
                limit=limit,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"知识库检索失败：{exc}",
        ) from exc

    return {
        "query": analysis.original_query,
        "rewritten_query": analysis.rewritten_query,
        "detected_city": analysis.detected_city,
        "city_conflict": analysis.city_conflict,
        "results": results,
    }
@router.post(
    "/ask",
    response_model=KnowledgeAnswerResponse,
)
def ask_knowledge(
    payload: KnowledgeAskRequest,
):
    try:
        analysis = rewrite_search_query_safely(
            query=payload.query,
            requested_city=payload.city,
        )

        if analysis.city_conflict:
            return {
                "query": analysis.original_query,
                "rewritten_query": analysis.rewritten_query,
                "detected_city": analysis.detected_city,
                "city_conflict": True,
                "answer": (
                    "问题中的城市与指定城市不一致，"
                    "请确认后重新提问。"
                ),
                "citations": [],
            }

        results = get_rag_service().search(
            query=analysis.rewritten_query,
            city=payload.city,
            limit=payload.limit,
        )

        answer = generate_knowledge_answer(
            query=payload.query,
            results=results,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"知识库问答失败：{exc}",
        ) from exc

    cited_results = select_cited_results(
        answer=answer,
        results=results,
    )

    citations = [
        {
            "title": result.title,
            "source": result.source,
            "chunk_index": result.chunk_index,
            "excerpt": result.text[:200],
            "score": result.final_score,
        }
        for result in cited_results
    ]

    return {
        "query": analysis.original_query,
        "rewritten_query": analysis.rewritten_query,
        "detected_city": analysis.detected_city,
        "city_conflict": False,
        "answer": answer,
        "citations": citations,
    }
