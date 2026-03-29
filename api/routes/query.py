import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
from search import hybrid
from llm.factory import get_provider
from core import manager
from core.settings import settings
from core.monitor import notify_user_activity

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


class QueryRequest(BaseModel):
    question: str
    history: list[dict] = []
    top_k: int | None = None
    file_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None


@router.post("/query")
async def rag_query(req: QueryRequest):
    notify_user_activity()
    top_k      = req.top_k or int(settings.get('search:rag_top_k') or 5)
    
    # We use a slightly lower threshold for RAG retrieval to give the LLM more context
    # but since RRF is a rank-based system, we rely on the top_k.

    hash_filter = manager.get_filtered_hashes(
        get_db(),
        file_type=req.file_type,
        date_from=req.date_from,
        date_to=req.date_to,
    )

    # Use Hybrid Search for RAG (FTS + Semantic)
    # Use keyword arguments to ensure correct parameter mapping
    try:
        results, _ = await hybrid.async_search(
            db_path=get_db(),
            query=req.question,
            top_k=top_k,
            file_type=req.file_type,
            date_from=req.date_from,
            date_to=req.date_to,
            hash_filter=hash_filter
        )

        chunks  = [r.get('chunk_text', '') for r in results]
        sources = [{'file_path': r.get('file_path'), 'score': r.get('score'), 'combined_score': r.get('combined_score')}
                   for r in results]

        llm    = get_provider()
        # rag_query is synchronous and blocks the thread; run in executor
        result = await asyncio.to_thread(llm.rag_query, req.question, chunks, history=req.history)

        return {'answer': result['answer'], 'thinking': result['thinking'], 'sources': sources}
    except Exception as e:
        print(f"[query] Error during RAG query: {e}")
        import traceback
        traceback.print_exc()
        raise e
