# Auth Store Backends

Orchid ships two storage backends for auth data (users, refresh tokens, API keys, OAuth accounts).
The backend is selected once at startup via environment variable — no code changes required.

---

## Backends

### FileUserStore (default)

JSON file at `~/.config/orchid/users.json`. Zero dependencies, zero setup.

```
~/.config/orchid/users.json
~/.config/orchid/audit/audit-YYYY-MM-DD.jsonl   ← audit log (separate, always file-based)
```

**Use when:** single-node, self-hosted, small team, no shared DB.

### PostgresUserStore

PostgreSQL-backed. Tables created automatically on first start (prefixed `orchid_`).
Requires `psycopg2-binary`.

**Use when:** multiple orchid nodes sharing one DB, enterprise deployment, high concurrency.

---

## Switching to PostgreSQL

### 1. Install the driver

```bash
source .venv/bin/activate
uv pip install 'orchid[postgres]'
```

Or if using the installed tool:

```bash
uv tool install --reinstall --from . --extra postgres orchid
```

### 2. Set the DSN

Add to `~/.config/orchid/.env`:

```bash
ORCHID_AUTH_STORE_DSN=postgresql://user:pass@host:5432/orchid
```

Standard libpq connection strings are accepted:

```bash
# With SSL
ORCHID_AUTH_STORE_DSN=postgresql://user:pass@host:5432/orchid?sslmode=require

# Unix socket (local)
ORCHID_AUTH_STORE_DSN=postgresql:///orchid?host=/var/run/postgresql
```

### 3. Migrate existing users (if switching from file)

If you have users in `users.json`, import them before cutting over:

```bash
source .venv/bin/activate
python - <<'EOF'
import json, os
from pathlib import Path

os.environ["ORCHID_AUTH_STORE_DSN"] = "postgresql://user:pass@host:5432/orchid"

from orchid.auth.store import FileUserStore, get_store

file_store = FileUserStore()
pg_store = get_store()

for user in file_store.list_users():
    try:
        pg_store.add_user(user)
        print(f"  migrated user: {user.username}")
    except Exception as e:
        print(f"  skip {user.username}: {e}")

for rt in file_store._refresh_tokens.values():
    try:
        pg_store.store_refresh_token(rt)
    except Exception:
        pass

for key in file_store._api_keys.values():
    try:
        pg_store.store_api_key(key)
    except Exception:
        pass

for oa in file_store._oauth_accounts.values():
    try:
        pg_store.store_oauth_account(oa)
    except Exception:
        pass

print("Done.")
EOF
```

### 4. Restart the service

```bash
sudo systemctl restart orchid-serve
```

Verify the backend loaded:

```bash
sudo journalctl -u orchid-serve | grep "Auth store"
# INFO  orchid.auth.store: Auth store: PostgresUserStore
```

---

## Switching back to file

Remove or unset `ORCHID_AUTH_STORE_DSN` from `~/.config/orchid/.env`, then restart.

---

## Schema reference

Tables created by `PostgresUserStore.__init__()`:

| Table | Contents |
|-------|----------|
| `orchid_users` | User accounts |
| `orchid_refresh_tokens` | Active refresh tokens (hashed) |
| `orchid_api_keys` | API key records (hashed secrets) |
| `orchid_oauth_accounts` | OIDC account links |

Audit log is **always file-based** (`~/.config/orchid/audit/`) regardless of store backend.

---

## Connection pool

`PostgresUserStore` defaults to a `psycopg2.pool.ThreadedConnectionPool` with:

| Setting | Default |
|---------|---------|
| `minconn` | 2 |
| `maxconn` | 10 |

For high-concurrency deployments, set a larger pool by subclassing or patching at startup:

```python
# In a custom entrypoint
from orchid.auth.store_postgres import PostgresUserStore
from orchid.auth import store as _store_mod

_store_mod._store_instance = PostgresUserStore(dsn, minconn=5, maxconn=50)
```
