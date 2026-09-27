# Installed Mem0 2.2.0 audit

This audit is intentionally based on the code installed into the Python 3.11
environment, not on version-agnostic documentation. Run it again with:

```bash
cd backend
.venv/bin/python inspect_mem0.py
```

Findings:

- `MongoDB.search()` builds `$vectorSearch` and then inserts a `$match` stage at
  pipeline position 1. User filtering therefore happens after approximate
  nearest-neighbor candidate selection and can hide valid results in a
  multi-user collection.
- `Memory.search()` requires an identity in `filters`, but that API-level guard
  does not correct the adapter's post-vector filtering.
- `Memory.update(memory_id, ...)` and `Memory.delete(memory_id)` are ID-only.
  They have no user-scope argument and do not authorize ownership themselves.
- `Memory.history(memory_id)` is also ID-only. History is stored through the
  configured Mem0 history database, which is SQLite in this setup.
- `delete_all(user_id=...)` is scoped and repeatedly lists/deletes batches.

Context Passport compensates in two places:

1. `ScopedMongoDB` places exact identity predicates inside
   `$vectorSearch.filter`, and creates Atlas vector filter fields for
   `payload.user_id`, `payload.agent_id`, and `payload.run_id`.
2. `MemoryManager` verifies ownership before update, delete, and history calls.

The local SQLite history file is suitable for this development gate. It is not
a horizontally scalable production history store; a later phase should move
history to a durable shared database before running multiple Render instances.

## Edit, Delete, and History Lifecycle Verification

The exact behavior of Mem0 2.2.0 during updates and deletions was empirically
verified via `backend/test_mem0_history_audit.py`:

1. **Initial Memory Creation (`ADD`)**:
   - Ingesting a memory creates a row in the SQLite `history` table:
     `event = 'ADD'`, `old_memory = NULL`, `new_memory = '<content>'`, `is_deleted = 0`.
2. **Memory Modification (`UPDATE`)**:
   - Modifying a memory (e.g. from `"The project uses MongoDB."` to `"The project uses PostgreSQL."`)
     inserts a new row into `history`:
     `event = 'UPDATE'`, `old_memory = 'The project uses MongoDB.'`, `new_memory = 'The project uses PostgreSQL.'`, `is_deleted = 0`.
   - The original text remains permanently stored in `old_memory`.
3. **Memory Deletion (`DELETE`)**:
   - Deleting the memory removes the vector document from MongoDB, but in SQLite history:
     `event = 'DELETE'`, `old_memory = 'The project uses PostgreSQL.'`, `new_memory = NULL`, `is_deleted = 1`.
   - The past history rows are **never purged**. Replaced text and deleted text both remain
     accessible in plaintext inside the SQLite `history` table.

**Context Passport safeguard**: `MemoryManager.delete()` now verifies ownership,
blocks retrieval, calls Mem0's deletion, and removes all `history` rows for that
memory ID from the configured SQLite database. The purge is verified before the
API reports success. `delete_all()` also purges history rows for deleted user
memories. The V3 Forget flow must additionally prevent queued capture events
from recreating a deleted memory before it can claim complete forgetting.
