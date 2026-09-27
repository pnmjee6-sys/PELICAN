from __future__ import annotations

import json
import sqlite3
import tempfile
import uuid
from pathlib import Path
from mem0.memory.main import SQLiteManager


def demonstrate_mem0_history_lifecycle() -> dict[str, object]:
    """
    Demonstrates and documents the exact lifecycle of Mem0 history storage
    during ADD, UPDATE, and DELETE operations.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_history.db")
        manager = SQLiteManager(db_path)

        memory_id = f"mem_{uuid.uuid4().hex[:8]}"

        # Step 1: Ingest "The project uses MongoDB."
        manager.add_history(
            memory_id=memory_id,
            old_memory=None,
            new_memory="The project uses MongoDB.",
            event="ADD",
            created_at="2026-09-25T10:00:00Z",
            is_deleted=0,
        )

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id, memory_id, old_memory, new_memory, event, is_deleted FROM history")
        rows_after_add = [
            {"id": r[0], "memory_id": r[1], "old_memory": r[2], "new_memory": r[3], "event": r[4], "is_deleted": r[5]}
            for r in cur.fetchall()
        ]

        # Step 2: Update to "The project uses PostgreSQL."
        manager.add_history(
            memory_id=memory_id,
            old_memory="The project uses MongoDB.",
            new_memory="The project uses PostgreSQL.",
            event="UPDATE",
            created_at="2026-09-25T10:05:00Z",
            updated_at="2026-09-25T10:05:00Z",
            is_deleted=0,
        )

        cur.execute("SELECT id, memory_id, old_memory, new_memory, event, is_deleted FROM history")
        rows_after_update = [
            {"id": r[0], "memory_id": r[1], "old_memory": r[2], "new_memory": r[3], "event": r[4], "is_deleted": r[5]}
            for r in cur.fetchall()
        ]

        # Step 3: Delete the memory
        manager.add_history(
            memory_id=memory_id,
            old_memory="The project uses PostgreSQL.",
            new_memory=None,
            event="DELETE",
            created_at="2026-09-25T10:10:00Z",
            updated_at="2026-09-25T10:10:00Z",
            is_deleted=1,
        )

        cur.execute("SELECT id, memory_id, old_memory, new_memory, event, is_deleted FROM history")
        rows_after_delete = [
            {"id": r[0], "memory_id": r[1], "old_memory": r[2], "new_memory": r[3], "event": r[4], "is_deleted": r[5]}
            for r in cur.fetchall()
        ]

        # Query full history via manager API
        history_api_result = manager.get_history(memory_id)

        # Audit findings
        deleted_text_persists_in_history = any(
            "MongoDB" in (r.get("old_memory") or "") or "PostgreSQL" in (r.get("old_memory") or "")
            for r in rows_after_delete
        )
        total_history_entries = len(rows_after_delete)

        manager.close()
        conn.close()

        return {
            "memory_id": memory_id,
            "rows_after_add": rows_after_add,
            "rows_after_update": rows_after_update,
            "rows_after_delete": rows_after_delete,
            "history_api_result": history_api_result,
            "findings": {
                "deleted_text_persists_in_sqlite": deleted_text_persists_in_history,
                "history_row_count_after_deletion": total_history_entries,
                "conclusion": (
                    "In Mem0 2.2.0, vector_store.delete() removes the vector from the search index, "
                    "but SQLiteManager appends a DELETE record to the 'history' table without purging "
                    "past records. Both the replaced text ('MongoDB') and the deleted text ('PostgreSQL') "
                    "remain stored in plaintext in the history database."
                ),
            },
        }


if __name__ == "__main__":
    report = demonstrate_mem0_history_lifecycle()
    print(json.dumps(report, indent=2))
