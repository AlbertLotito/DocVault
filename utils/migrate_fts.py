"""
DocVault migration utility: Backfill chunk-level FTS index.
Iterates through all COMPLETED tasks, re-chunks their extracted text,
and populates the new granular fts_index.
"""
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import manager
from embeddings.chunker import chunk

def migrate():
    db_path = manager.get_db_path()
    print(f"Starting FTS migration on {db_path}...")

    with manager._connect(db_path) as conn:
        # 1. Drop and recreate with new schema
        print("Recreating FTS index with chunk-level schema...")
        conn.execute("DROP TABLE IF EXISTS fts_index")
        conn.execute("""
            CREATE VIRTUAL TABLE fts_index USING fts5(
                file_hash UNINDEXED,
                chunk_index UNINDEXED,
                file_path UNINDEXED,
                content
            )
        """)
        
        # 2. Fetch all completed tasks with text
        print("Fetching COMPLETED tasks...")
        tasks = conn.execute(
            "SELECT file_hash, file_path, extracted_text FROM tasks WHERE status='COMPLETED' AND extracted_text IS NOT NULL"
        ).fetchall()
        
        total = len(tasks)
        print(f"Found {total} documents to re-index.")
        
        indexed_count = 0
        chunk_count = 0
        
        for i, task in enumerate(tasks):
            h = task['file_hash']
            path = task['file_path']
            text = task['extracted_text']
            
            try:
                chunks = chunk(text)
                for idx, c_text in enumerate(chunks):
                    conn.execute(
                        "INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES (?, ?, ?, ?)",
                        (h, idx, path, c_text)
                    )
                    chunk_count += 1
                
                indexed_count += 1
                if indexed_count % 100 == 0:
                    print(f"  Progress: {indexed_count}/{total} documents indexed...")
            except Exception as e:
                print(f"  [ERROR] Failed to index {path}: {e}")

        conn.commit()
        print(f"Migration complete!")
        print(f"Documents processed: {indexed_count}")
        print(f"Chunks indexed:     {chunk_count}")

if __name__ == "__main__":
    migrate()
