"""
ArtVectorStore — LanceDB-backed store for the CLIP art identification index.

Separate table from the main document store (embeddings/vector_store.py):
different vector dimension (512, CLIP ViT-B/32 vs 768, nomic-embed-text) and
a different payload shape (artist/title/style/genre vs chunk text). Lives in
the same lancedb_storage/ directory as its own table, 'art_index'.
"""
import lancedb
import pyarrow as pa
from embeddings.vector_store import _db_path

CLIP_DIM = 512

_SCHEMA = pa.schema([
    pa.field('id',         pa.string()),
    pa.field('vector',     pa.list_(pa.float32(), CLIP_DIM)),
    pa.field('artist',     pa.string()),
    pa.field('title',      pa.string()),
    pa.field('style',      pa.string()),
    pa.field('genre',      pa.string()),
    pa.field('source_url', pa.string()),
    pa.field('dataset',    pa.string()),
])


class ArtVectorStore:
    def __init__(self, table_name: str = 'art_index'):
        self.table_name = table_name
        self._db = lancedb.connect(_db_path())
        self._table = None  # lazy — table may not exist yet (index not built)

    def _open(self):
        if self._table is None and self.table_name in self._db.list_tables().tables:
            self._table = self._db.open_table(self.table_name)
        return self._table

    def _ensure_table(self):
        if self._table is None:
            if self.table_name in self._db.list_tables().tables:
                self._table = self._db.open_table(self.table_name)
            else:
                self._table = self._db.create_table(self.table_name, schema=_SCHEMA)
        return self._table

    def exists(self) -> bool:
        return self.table_name in self._db.list_tables().tables

    # ── Write ──────────────────────────────────────────────────────────────────

    def upsert_batch(self, ids: list[str], vectors: list[list[float]],
                      payloads: list[dict]) -> None:
        if not ids:
            return
        table = self._ensure_table()
        rows = [
            {
                'id':         i,
                'vector':     v,
                'artist':     p.get('artist', '') or '',
                'title':      p.get('title', '') or '',
                'style':      p.get('style', '') or '',
                'genre':      p.get('genre', '') or '',
                'source_url': p.get('source_url', '') or '',
                'dataset':    p.get('dataset', '') or '',
            }
            for i, v, p in zip(ids, vectors, payloads)
        ]
        (
            table.merge_insert('id')
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def search(self, query_vector: list[float], top_k: int = 1) -> list[dict]:
        """Return up to top_k nearest neighbours, or [] if the table doesn't exist yet."""
        table = self._open()
        if table is None:
            return []
        rows = (
            table.search(query_vector, vector_column_name='vector')
            .metric('cosine')
            .limit(top_k)
            .to_list()
        )
        results = []
        for r in rows:
            # LanceDB cosine returns distance (0=identical, 2=opposite).
            score = 1.0 - (r['_distance'] / 2.0)
            results.append({
                'score':      score,
                'artist':     r['artist'],
                'title':      r['title'],
                'style':      r['style'],
                'genre':      r['genre'],
                'source_url': r['source_url'],
                'dataset':    r['dataset'],
            })
        return results

    # ── Admin ──────────────────────────────────────────────────────────────────

    def count(self) -> int:
        table = self._open()
        return table.count_rows() if table is not None else 0
