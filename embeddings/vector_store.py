import os
import lancedb
import pyarrow as pa
from core.settings import settings


def _db_path() -> str:
    p = settings.get('lancedb:path') or 'lancedb_storage'
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.dirname(__file__)), p)
    os.makedirs(p, exist_ok=True)
    return p


# Above this many hashes, skip the inline SQL `IN (...)` filter (see search()).
_HASH_FILTER_INLINE_MAX = 500
_POST_FILTER_OVERFETCH = 20

_SCHEMA = pa.schema([
    pa.field('id',          pa.string()),
    pa.field('file_hash',   pa.string()),
    pa.field('chunk_index', pa.int32()),
    pa.field('file_path',   pa.string()),
    pa.field('chunk_text',  pa.string()),
    pa.field('vector',      pa.list_(pa.float32(), 768)),
])


class VectorStore:
    def __init__(self, collection: str = 'docvault', vector_size: int = 768):
        self.collection = collection
        self._vector_size = vector_size
        self._table = self._ensure_table()

    def _ensure_table(self):
        db = lancedb.connect(_db_path())
        if self.collection in db.list_tables().tables:
            return db.open_table(self.collection)
        return db.create_table(self.collection, schema=_SCHEMA)

    # ── Write ──────────────────────────────────────────────────────────────────

    def upsert(self, file_hash: str, chunk_index: int,
               vector: list[float], payload: dict):
        self.upsert_batch(file_hash, [{'chunk_index': chunk_index,
                                       'vector': vector, 'payload': payload}])

    def upsert_batch(self, file_hash: str, chunks: list[dict]) -> None:
        if not chunks:
            return
        rows = [
            {
                'id':          f"{file_hash}:{c['chunk_index']}",
                'file_hash':   file_hash,
                'chunk_index': c['chunk_index'],
                'file_path':   c['payload'].get('file_path', ''),
                'chunk_text':  c['payload'].get('chunk_text', ''),
                'vector':      c['vector'],
            }
            for c in chunks
        ]
        (
            self._table.merge_insert('id')
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    def upsert_documents(self, doc_batches: dict) -> None:
        """Write chunks for multiple documents in a single merge_insert call."""
        rows = []
        for file_hash, chunks in doc_batches.items():
            for c in chunks:
                rows.append({
                    'id':          f"{file_hash}:{c['chunk_index']}",
                    'file_hash':   file_hash,
                    'chunk_index': c['chunk_index'],
                    'file_path':   c['payload'].get('file_path', ''),
                    'chunk_text':  c['payload'].get('chunk_text', ''),
                    'vector':      c['vector'],
                })
        if rows:
            (
                self._table.merge_insert('id')
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(rows)
            )

    def update_path(self, file_hash: str, new_path: str):
        self._table.update(
            where=f"file_hash = '{_esc(file_hash)}'",
            values={'file_path': new_path},
        )

    def delete_by_hash(self, file_hash: str):
        self._table.delete(f"file_hash = '{_esc(file_hash)}'")

    def delete_by_hashes(self, file_hashes: list[str]):
        if not file_hashes:
            return
        escaped = ', '.join(f"'{_esc(h)}'" for h in file_hashes)
        self._table.delete(f"file_hash IN ({escaped})")


    # ── Read ───────────────────────────────────────────────────────────────────

    def search(self, query_vector: list[float],
               top_k: int = 5, score_threshold: float = None,
               hash_filter: list[str] = None) -> list[dict]:
        if hash_filter is not None and len(hash_filter) == 0:
            return []

        # Inlining a `file_hash IN (...)` clause for large filters builds a
        # multi-megabyte SQL string that LanceDB's planner takes 30s+ (or hangs)
        # to evaluate — that's what produced "Semantic search unavailable" once
        # a vault grew past ~100K documents. Past this size, fetch a larger
        # unfiltered candidate pool and filter by set membership in Python instead.
        post_filter_set = None
        fetch_limit = top_k

        q = self._table.search(query_vector, vector_column_name='vector').metric('cosine')
        if hash_filter is not None:
            if len(hash_filter) <= _HASH_FILTER_INLINE_MAX:
                escaped = ', '.join(f"'{_esc(h)}'" for h in hash_filter)
                q = q.where(f"file_hash IN ({escaped})", prefilter=True)
            else:
                post_filter_set = set(hash_filter)
                fetch_limit = top_k * _POST_FILTER_OVERFETCH

        rows = q.limit(fetch_limit).to_list()

        results = []
        for r in rows:
            if post_filter_set is not None and r['file_hash'] not in post_filter_set:
                continue
            # LanceDB cosine returns distance (0=identical, 2=opposite).
            # Convert to similarity score: score = 1 - (distance / 2)
            score = 1.0 - (r['_distance'] / 2.0)
            if score_threshold is not None and score < score_threshold:
                continue
            results.append({
                'score':       score,
                'file_hash':   r['file_hash'],
                'file_path':   r['file_path'],
                'chunk_text':  r['chunk_text'],
                'chunk_index': r['chunk_index'],
            })
            if len(results) >= top_k:
                break
        return results

    # ── Admin ──────────────────────────────────────────────────────────────────

    def drop_and_recreate(self):
        """Wipe all vectors and start fresh (used by Rebuild Index)."""
        db = lancedb.connect(_db_path())
        if self.collection in db.list_tables().tables:
            db.drop_table(self.collection)
        self._table = db.create_table(self.collection, schema=_SCHEMA)

    def create_vector_index(self) -> None:
        """Build (or rebuild) an IVF-PQ vector index for fast ANN search.

        Required once the table grows beyond a handful of rows — without it every
        search is a full linear scan (O(n) disk reads).  Safe to call on an already-
        indexed table (replace=True).  Takes O(minutes) for large tables; run it in
        a background thread so the server stays responsive.
        """
        self._table.create_index(
            metric='cosine',
            vector_column_name='vector',
            replace=True,
        )

    def count(self) -> int:
        return self._table.count_rows()


def _esc(s: str) -> str:
    """Minimal SQL string escaping for LanceDB filter expressions."""
    return s.replace("'", "''")
