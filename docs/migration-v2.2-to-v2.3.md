# Migration Guide: V2.2.4 → V2.3

**Time required:** ~15 minutes (no OAuth) / ~30 minutes (with OAuth SSO)

---

## What Changed

| Area | V2.2.4 | V2.3 |
|------|--------|------|
| Auth tokens | In-memory dict, lost on restart | HS256 JWT (15 min) + persisted refresh tokens (30 days) |
| Passwords | Accepted but never stored or verified | argon2id hashed, verified on login |
| API keys | Not implemented | `ok_{id}.{secret}` format, scoped |
| SSO | Not implemented | Google, Entra ID, any OIDC |
| Mobile auth | Not implemented | PKCE S256 flow |
| Audit log | Not implemented | Append-only JSONL, daily rotation |
| User management | Admin list only | `PUT/DELETE /api/auth/users/{id}` |
| Project scoping | Not enforced | `User.projects` list enforced on task runs |
| `users.json` sections | `{users: [...]}` | `{users, refresh_tokens, api_keys, oauth_accounts}` |
| Required env vars | `ANTHROPIC_API_KEY` | + `JWT_SECRET` |
| New dependencies | — | `argon2-cffi`, `PyJWT`, `authlib` |

---

## Breaking Changes

1. **`JWT_SECRET` env var required** — auth endpoints raise `RuntimeError` without it.
2. **Existing users have no password** — `password_hash` is `null` in migrated records; they cannot log in until a password is set.
3. **Old bearer tokens are invalid** — the in-memory `_auth_tokens` dict is gone. All active sessions are terminated.
4. **`User.token` field is now optional** (default `""`). Code that constructs `User(user_id=…, token=…)` still works; the field is kept for backward compat but unused by the auth system.

---

## Step-by-Step Migration

### 1. Stop the running service

```bash
sudo systemctl stop orchid-serve
```

Verify it stopped:
```bash
sudo systemctl status orchid-serve
```

---

### 2. Pull the new code

```bash
cd ~/LocalAI/orchid
git pull origin main
```

Confirm you're on the right commit:
```bash
orchid --version
# orchid 2.2.4 (commit 285bb72, ...)
```

---

### 3. Install new dependencies

```bash
cd ~/LocalAI/orchid
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Verify the three new packages installed:
```bash
python -c "import argon2, jwt, authlib; print('OK')"
```

---

### 4. Add `JWT_SECRET` to your env file

Generate a secret:
```bash
openssl rand -hex 32
```

Add to `~/.config/orchid/.env`:
```bash
JWT_SECRET=<paste-the-output-here>
```

**Security rules:**
- Minimum 32 bytes (64 hex chars)
- Never commit to git
- All orchid instances sharing sessions must use the same secret
- Changing it invalidates all existing sessions

---

### 5. Migrate existing users

#### Option A — Re-register (simplest, recommended for small teams)

If you have few users, the easiest path is to re-register them with the new system after restart. Skip to step 6, start the service, then:

```bash
curl -s -X POST http://localhost:7842/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "new-strong-password", "role": "user"}'
```

#### Option B — Set passwords for existing users via migration script

Run this **before** starting the new service. It reads existing `users.json`, sets a temporary password for each user that lacks one, and writes the updated file. **Print the temporary passwords** — users must change them on first login.

```python
#!/usr/bin/env python3
"""Set temporary passwords for V2.2 users that have no password_hash."""
import json, secrets, string
from pathlib import Path

# Activate the orchid venv before running this script:
#   source .venv/bin/activate && python scripts/migrate_users.py

from argon2 import PasswordHasher

USERS_FILE = Path.home() / ".config/orchid/users.json"
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

data = json.loads(USERS_FILE.read_text()) if USERS_FILE.exists() else {"users": []}
changed = []

for user in data.get("users", []):
    if not user.get("password_hash"):
        pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        user["password_hash"] = ph.hash(pw)
        changed.append((user.get("username", user["user_id"]), pw))

# Ensure new sections exist
data.setdefault("refresh_tokens", [])
data.setdefault("api_keys", [])
data.setdefault("oauth_accounts", [])

USERS_FILE.write_text(json.dumps(data, indent=2, default=str))

if changed:
    print("\nTemporary passwords set (share securely, force change on first login):\n")
    for username, pw in changed:
        print(f"  {username:20s}  {pw}")
    print()
else:
    print("No users required migration.")
```

Save as `scripts/migrate_users.py` and run:
```bash
source .venv/bin/activate
python scripts/migrate_users.py
```

---

### 6. Update the systemd service environment file

The service's `EnvironmentFile` must include `JWT_SECRET`. If your service uses `~/.config/orchid/.env` (the default), you've already done this in step 4.

If you have a custom `EnvironmentFile` path, add `JWT_SECRET` there too.

Reload systemd to pick up any service-file changes:
```bash
sudo systemctl daemon-reload
```

---

### 7. Start the service

```bash
sudo systemctl start orchid-serve
sudo journalctl -u orchid-serve -f
```

Watch for errors. Expected healthy startup log:
```
INFO  orchid.serve: Starting orchid serve on port 7842
INFO  uvicorn: Application startup complete.
```

If you see `RuntimeError: JWT_SECRET environment variable not set`, the env var isn't reaching the service — check the `EnvironmentFile` path in the service unit.

---

### 8. Verify auth is working

```bash
# Register (or skip if you already have users)
curl -s -X POST http://localhost:7842/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password", "role": "admin"}'

# Login — should set cookies and return access_token
curl -s -c /tmp/orchid-cookies.txt \
  -X POST http://localhost:7842/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password"}'

# Verify session
curl -s -b /tmp/orchid-cookies.txt http://localhost:7842/api/auth/me
# → {"authenticated": true, "username": "admin", ...}

# Check version includes git hash
orchid --version
# → orchid 2.2.4 (commit 285bb72, 2026-05-09 ...)
```

---

### 9. (Optional) Configure OAuth SSO

If you want Google / Entra ID / OIDC login, add providers to `~/.config/orchid/config.yaml` (create if it doesn't exist):

```yaml
auth:
  providers:
    - type: google
      client_id: "${GOOGLE_CLIENT_ID}"
      client_secret: "${GOOGLE_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/google/callback"

    - type: entra
      tenant_id: "${AZURE_TENANT_ID}"
      client_id: "${AZURE_CLIENT_ID}"
      client_secret: "${AZURE_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/entra/callback"
```

Add the corresponding env vars to `~/.config/orchid/.env`, then restart the service.

Test:
```bash
curl http://localhost:7842/api/auth/oauth/providers
# → {"providers": ["google", "entra"]}
```

---

### 10. (Optional) Set up per-user project scoping

After login, admins can restrict which projects a user can run tasks against:

```bash
curl -s -b /tmp/orchid-cookies.txt \
  -X PUT http://localhost:7842/api/auth/users/alice \
  -H "Content-Type: application/json" \
  -d '{"projects": ["myapp", "api-service"]}'
```

Empty `projects` list = unrestricted. Admins always bypass scoping.

---

### 11. (Optional) Create API keys for CI/scripts

```bash
curl -s -b /tmp/orchid-cookies.txt \
  -X POST http://localhost:7842/api/auth/apikeys \
  -H "Content-Type: application/json" \
  -d '{"name": "github-actions", "scopes": ["tasks:run"]}'
# → {"key_id": "...", "secret": "ok_...", ...}
# Secret is shown ONCE — store it in your CI secrets vault immediately
```

---

## Rollback

If you need to revert:

```bash
sudo systemctl stop orchid-serve
cd ~/LocalAI/orchid
git checkout c450ef4          # last commit before auth work
source .venv/bin/activate
uv pip install -e ".[dev]"    # downgrades new deps
sudo systemctl start orchid-serve
```

The `users.json` file written by V2.3 is forward-compatible with V2.2 — extra fields (`password_hash`, `refresh_tokens`, etc.) are ignored by the old `UserStore._load()`.

---

## New Files & Directories

| Path | Created by | Purpose |
|------|-----------|---------|
| `~/.config/orchid/audit/audit-YYYY-MM-DD.jsonl` | First auth event | Append-only audit log |
| `scripts/deploy.sh` | This release | Version bump + git tag + push |
| `orchid/auth/jwt.py` | This release | JWT + password + API key utilities |
| `orchid/auth/audit.py` | This release | AuditStore + AuditAction constants |
| `orchid/auth/providers/` | This release | OIDC provider package |

---

## New API Endpoints Summary

```
POST   /api/auth/refresh                      rotate refresh token
POST   /api/auth/apikeys                      create API key
GET    /api/auth/apikeys                      list API keys
DELETE /api/auth/apikeys/{key_id}             revoke API key
PUT    /api/auth/users/{id}                   update user (admin)
DELETE /api/auth/users/{id}                   deactivate user (admin)
GET    /api/audit                             paginated audit log (admin)
GET    /api/auth/oauth/providers              list configured SSO providers
GET    /api/auth/oauth/{provider}/start       begin OAuth flow
GET    /api/auth/oauth/{provider}/callback    web OAuth callback
POST   /api/auth/oauth/{provider}/callback    POST OAuth callback
POST   /api/auth/oauth/{provider}/token       mobile PKCE token exchange
POST   /api/projects/{id}/run/authenticated   scope-gated project run
GET    /api/projects/{id}/stream/sse          SSE stream (mobile-compatible)
```

---

## Common Migration Issues

**`RuntimeError: JWT_SECRET environment variable not set`**
`JWT_SECRET` is missing from the env the service reads. Check `EnvironmentFile=` in the systemd unit and that the file contains `JWT_SECRET=…`.

**Login returns 401 "Invalid credentials" for existing users**
The user record has no `password_hash`. Run the migration script (Step 5, Option B) or re-register the user.

**Old Telegram/Slack whitelist still works?**
Yes — `TELEGRAM_ALLOWED_USERS` integer whitelist (D0015) is unchanged. The new auth system is additive; bot interfaces are unaffected.

**Audit directory not created**
It's created on the first auth event. If you need it pre-created: `mkdir -p ~/.config/orchid/audit`.

**`orchid --version` shows wrong version number**
The version comes from the installed package metadata, not `pyproject.toml`. Run `uv pip install -e .` to refresh it.
