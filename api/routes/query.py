import asyncio
import json
import threading
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
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
    vault_ids: str | None = None


@router.post("/query")
async def rag_query(req: QueryRequest):
    notify_user_activity()
    db = get_db()
    top_k      = req.top_k or int(settings.get('search:rag_top_k') or 5)
    vault_id_list = [v.strip() for v in req.vault_ids.split(',') if v.strip()] if req.vault_ids else None

    # We use a slightly lower threshold for RAG retrieval to give the LLM more context
    # but since RRF is a rank-based system, we rely on the top_k.

    hash_filter = manager.get_filtered_hashes(
        db,
        file_type=req.file_type,
        date_from=req.date_from,
        date_to=req.date_to,
        vault_ids=vault_id_list,
    )

    # Use Hybrid Search for RAG (FTS + Semantic)
    # Use keyword arguments to ensure correct parameter mapping
    try:
        results, _ = await hybrid.async_search(
            db_path=db,
            query=req.question,
            top_k=top_k,
            file_type=req.file_type,
            date_from=req.date_from,
            date_to=req.date_to,
            hash_filter=hash_filter,
            vault_ids=vault_id_list,
        )

        # Substitute vault-specific file paths for single-vault RAG queries
        try:
            manager.substitute_vault_paths(db, results, vault_id_list)
        except Exception:
            pass  # get_vault_paths already returns {} on error; belt-and-suspenders

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


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/query/stream")
async def rag_query_stream(req: QueryRequest):
    """SSE endpoint — yields tokens as they arrive from the LLM."""
    notify_user_activity()
    db = get_db()
    top_k         = req.top_k or int(settings.get('search:rag_top_k') or 5)
    vault_id_list = [v.strip() for v in req.vault_ids.split(',') if v.strip()] if req.vault_ids else None

    async def event_stream():
        try:
            yield _sse({'type': 'status', 'text': 'Searching documents...'})

            hash_filter = manager.get_filtered_hashes(
                db,
                file_type=req.file_type,
                date_from=req.date_from,
                date_to=req.date_to,
                vault_ids=vault_id_list,
            )
            results, _ = await hybrid.async_search(
                db_path=db,
                query=req.question,
                top_k=top_k,
                file_type=req.file_type,
                date_from=req.date_from,
                date_to=req.date_to,
                hash_filter=hash_filter,
                vault_ids=vault_id_list,
            )
            try:
                manager.substitute_vault_paths(db, results, vault_id_list)
            except Exception:
                pass

            chunks  = [r.get('chunk_text', '') for r in results]
            sources = [
                {'file_path': r.get('file_path'), 'score': r.get('score'),
                 'combined_score': r.get('combined_score')}
                for r in results
            ]

            yield _sse({'type': 'status', 'text': 'Generating answer...'})

            llm      = get_provider()
            messages = llm._build_rag_messages(req.question, chunks, history=req.history)

            loop    = asyncio.get_running_loop()
            token_q: asyncio.Queue = asyncio.Queue()

            def _run_stream():
                try:
                    for chunk in llm.chat_stream(messages):
                        loop.call_soon_threadsafe(token_q.put_nowait, chunk)
                except Exception as exc:
                    loop.call_soon_threadsafe(token_q.put_nowait, {'__error__': str(exc)})
                finally:
                    loop.call_soon_threadsafe(token_q.put_nowait, None)

            threading.Thread(target=_run_stream, daemon=True).start()

            full_text = ''
            raw_timeout = settings.get('ollama:chat_timeout')
            stream_timeout = (int(raw_timeout) if raw_timeout not in (None, '', '0', 0) else 300) + 10

            while True:
                item = await asyncio.wait_for(token_q.get(), timeout=stream_timeout)
                if item is None:
                    break
                if isinstance(item, dict) and '__error__' in item:
                    yield _sse({'type': 'error', 'text': item['__error__']})
                    return
                full_text += item
                yield _sse({'type': 'token', 'text': item})

            answer, thinking = llm._extract_thinking(full_text)
            yield _sse({'type': 'done', 'answer': answer, 'thinking': thinking, 'sources': sources})

        except asyncio.TimeoutError:
            yield _sse({'type': 'error', 'text': 'Timed out waiting for the LLM response.'})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            yield _sse({'type': 'error', 'text': str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
