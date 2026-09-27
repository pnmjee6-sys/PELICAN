from __future__ import annotations

import inspect
import json
from importlib.metadata import version

from mem0.memory.main import Memory
from mem0.vector_stores.mongodb import MongoDB


def inspect_installed() -> dict[str, object]:
    search_source = inspect.getsource(MongoDB.search)
    update_source = inspect.getsource(Memory.update)
    delete_source = inspect.getsource(Memory.delete)
    history_source = inspect.getsource(Memory.history)
    return {
        "mem0ai_version": version("mem0ai"),
        "mongodb_filter_is_post_vector_search": "pipeline.insert(1" in search_source,
        "update_accepts_user_scope": "user_id" in inspect.signature(Memory.update).parameters,
        "delete_accepts_user_scope": "user_id" in inspect.signature(Memory.delete).parameters,
        "history_accepts_user_scope": "user_id" in inspect.signature(Memory.history).parameters,
        "update_checks_by_id_only": "self._update_memory(memory_id" in update_source,
        "delete_checks_by_id_only": "vector_store.get(vector_id=memory_id)" in delete_source,
        "history_uses_local_history_db": "self.db.get_history(memory_id)" in history_source,
        "context_passport_fixes": [
            "ScopedMongoDB puts identity filters inside $vectorSearch.filter",
            "MemoryManager checks ownership before get/update/delete/history",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(inspect_installed(), indent=2))
