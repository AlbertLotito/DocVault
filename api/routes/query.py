from fastapi import APIRouter
from pydantic import BaseModel
from search import semantic
from llm.factory import get_provider
from core import manager
from core.settings import settings

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
def rag_query(req: QueryRequest):
    top_k      = req.top_k or int(settings.get('search:rag_top_k') or 5)
    rag_thresh = float(settings.get('search:rag_threshold') or 0.50)

    hash_filter = manager.get_filtered_hashes(
        get_db(),
        file_type=req.file_type,
        date_from=req.date_from,
        date_to=req.date_to,
    )

    results = semantic.search(req.question, top_k=top_k, hash_filter=hash_filter,
                              score_threshold=rag_thresh)
    chunks  = [r.get('chunk_text', '') for r in results]
    sources = [{'file_path': r.get('file_path'), 'score': r.get('score')}
               for r in results]

    llm    = get_provider()
    result = llm.rag_query(req.question, chunks, history=req.history)

    return {'answer': result['answer'], 'thinking': result['thinking'], 'sources': sources}
