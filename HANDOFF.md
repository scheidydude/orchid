# HANDOFF.md
_Written: 2026-05-26. Previous HANDOFF archived as `HANDOFF-archive-2026-05-10-0000.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework. This session began work on transforming it into a true **multi-user agentic OS** — multiple people on one deployment, each with their own dashboard, scheduled tasks, and (eventually) credentials. Phase 1 of that roadmap is done: users get a separate React portal at `/app`, admins keep the existing power-user UI at `/`. The next chunk is Phase 2: credential vault + per-user notification config.

---

## 2. Current State

### What's working and verified at commit `14b775a`

**Portal SPA (`/app`):**
- `orchid/interfaces/portal/` — Vite + React app, built and tested
- `dist/` exists locally (gitignored) — run `cd orchid/interfaces/portal && npm install && npm run build` to rebuild
- Served at `/app` and `/app/*` by `web_server.py`
- Pages: Dashboard (scheduled tasks + projects), Settings (profile, password change, API key manager)
- Role-based redirect at `/`: non-admin authed users → 302 `/app/`; admins stay at `/`
- Admin sees "Admin Console →" link in portal user menu pointing back to `/`

**Password change endpoint:**
- `PUT /api/auth/me/password` — wired in `web_server.py`, tested, working
- Verifies current password (argon2), enforces 8-char minimum, calls `store.update_user()`

**Test suite:**
- `tests/test_portal_api.py` — 8 tests: password change (success, wrong current, too short, missing fields, unauthenticated), portal routing smoke tests
- `44 passed` across `test_web.py`, `test_web_v2.py`, `test_portal_api.py`

**Docs:**
- `docs/multiuser-review.md` — gap analysis of current multi-user support
- `docs/multiuser-proposal.md` — full 5-phase roadmap with resolved decisions

### What's half-built / gaps in Phase 1

- **Portal `dist/` not committed** (gitignored). Before deploying, run the build command in `orchid/interfaces/portal/`.
- **UserSettings Phase 2 stubs** — "Credentials" and "Notifications" sections in `UserSettings.jsx` are placeholder cards with "coming in Phase 2" labels. No backend yet.
- **`POST /api/auth/me/password` not in `orchid/web/server.py`** — the OLD server file (never used by `orchid serve`) doesn't have it. Fine, `web_server.py` is the real one.
- **Portal not linked from `orchid serve` build docs** — README not yet updated for Phase 1.

### Exact next action

Start Phase 2: Credential Vault + Per-User Config + Notifications. See proposal doc for full spec. First step: backend credential store at `~/.config/orchid/users/{user_id}/credentials.json.enc`.

---

## 3. Decisions Made (and Why)

**Decision:** One FastAPI app, two React SPAs (`/` and `/app/`), not separate servers
**Alternatives considered:** Separate server processes on different ports, single SPA with role-based tabs
**Reason:** One deploy, one auth session, one TLS cert. Admin can visit `/app/` to see the user view. No CORS, no second JWT secret, no second systemd unit.
**Reversibility:** Easy to split later if needed. The portal is already a separate Vite app.

**Decision:** Non-admin users redirected from `/` to `/app/` at the server layer (Python), not client-side
**Alternatives considered:** Client-side redirect in the existing React app after auth check
**Reason:** Server-side avoids a flash of the wrong UI. `_get_user_from_request()` reads the HttpOnly JWT cookie and does a 302 before any HTML is returned.
**Reversibility:** Easy. The helper is 10 lines in `web_server.py`.

**Decision:** Portal is a completely separate Vite project (`orchid/interfaces/portal/`), not a new route in `web_ui/`
**Alternatives considered:** Add portal pages as routes inside the existing `web_ui` app
**Reason:** Clean separation. Different target persona (end user vs power user/admin). Different nav, different component set. Shared API but no shared component code — copy-paste the few shared pieces (Login, CSS vars).
**Reversibility:** Could be merged later but no reason to.

**Decision:** `vite.config.js` uses `base: '/app/'` so all asset paths in the portal build are relative to `/app/`
**Alternatives considered:** Default base (`/`), letting the server rewrite paths
**Reason:** Required for the SPA to work when served at a sub-path. Without it, `index.html` loads assets at `/assets/...` which conflicts with the main app's `/assets/`.
**Reversibility:** Must keep this. Removing it breaks the portal build.

**Decision:** Admin users are NOT force-redirected away from the portal — they can visit `/app/` deliberately
**Alternatives considered:** Redirect admin users from `/app/` back to `/`
**Reason:** Admin being able to see exactly what users see is a feature (for debugging/support). Admin console link is in the portal user menu.
**Reversibility:** Easy to add a redirect if desired.

**Decision:** Postgres migration = Phase 5, not earlier
**Alternatives considered:** Phase 2 (alongside credential vault)
**Reason:** `FileUserStore` is fine for small teams. Adding Postgres dependency earlier blocks Phase 2–4 on infra setup. Phase 5 is the right time.
**Reversibility:** Decision stands unless user changes it.

**Decision:** No team/group model — project access is admin-controlled via `User.projects` list
**Alternatives considered:** Team entities with shared project pools
**Reason:** User explicitly chose this. Simpler, no new data model needed.
**Reversibility:** Could add team model in Phase 4+ but user has decided against it.

**Decision:** MCP credentials are per-user only, never shared
**Alternatives considered:** Shared credential pool for a "team inbox" style use case
**Reason:** User explicitly chose this. Simpler security model.
**Reversibility:** User decision — don't reopen.

**Decision:** User registration is admin-invite only
**Alternatives considered:** Open registration with admin approval
**Reason:** User explicitly chose this. Internal tool, not public SaaS.
**Reversibility:** User decision — don't reopen.

**Decision:** Phase 2 notifications = email + Telegram + Slack (per-user config)
**Alternatives considered:** Email only for Phase 2
**Reason:** User explicitly requested all three channels.
**Reversibility:** Easy to add/remove channels.

---

## 4. Architecture & Key Files

### Created this session

| File | What it does |
|------|-------------|
| `orchid/interfaces/portal/` | New Vite+React user portal app. Entry: `src/main.jsx`. Build: `npm run build`. Served at `/app/*`. |
| `orchid/interfaces/portal/src/App.jsx` | Auth gate + top-level routing (view state: `dashboard` \| `settings`). `useAuth` hook, `UserMenu` with Admin Console link. |
| `orchid/interfaces/portal/src/components/Dashboard.jsx` | Landing page: scheduled task rows (run now, history, edit, delete, create) + project cards with progress bars. |
| `orchid/interfaces/portal/src/components/TaskFormModal.jsx` | Create/edit scheduled task: name, type, schedule presets + cron input, JSON config editor, enable/notify toggles. |
| `orchid/interfaces/portal/src/components/TaskRunHistory.jsx` | Modal: table of runs with expand-to-show-output. Fetches `GET /api/scheduler/tasks/{id}/runs`. |
| `orchid/interfaces/portal/src/components/UserSettings.jsx` | Profile display, password change form, API key manager (lazy-loaded), Phase 2 stubs. |
| `orchid/interfaces/portal/src/components/Login.jsx` | Standalone login form for portal (identical UX to `web_ui` login). |
| `orchid/interfaces/portal/src/components/StatusBadge.jsx` | `StatusBadge`, `TypeBadge`, `RoleBadge` — shared badge components. |
| `orchid/interfaces/portal/src/hooks/useAuth.js` | `useAuth()` — GET `/api/auth/me`, returns `{user, checked, setUser, logout}`. |
| `orchid/interfaces/portal/src/hooks/useScheduledTasks.js` | `useScheduledTasks()` — wraps all scheduler API calls: list, run, delete, create, update, getRuns. |
| `orchid/interfaces/portal/src/hooks/useProjects.js` | `useProjects()` — GET `/api/projects`, returns `{projects, loading, error, refresh}`. |
| `orchid/interfaces/portal/src/index.css` | Full dark theme CSS matching `web_ui` design tokens. CSS custom properties, component classes (`.card`, `.badge`, `.modal`, etc.). |
| `orchid/interfaces/portal/package.json` | Portal deps: React 18, Vite 5. No chart libs, no router — state-based nav only. |
| `orchid/interfaces/portal/vite.config.js` | `base: '/app/'`, proxy to `localhost:7842`, builds to `dist/`. Dev port 5174. |
| `tests/test_portal_api.py` | 8 tests for `PUT /api/auth/me/password` and portal routing. |
| `docs/multiuser-review.md` | Gap analysis: what multi-user support exists vs what's needed. |
| `docs/multiuser-proposal.md` | 5-phase roadmap with resolved decisions table. |

### Modified significantly this session

| File | What changed |
|------|-------------|
| `orchid/interfaces/web_server.py` | Added `_PORTAL_DIST_DIR`, portal static file mounts (`/app/assets`, `/app`, `/app/*`), `_get_user_from_request()` helper, role-based 302 at `/`, `PUT /api/auth/me/password` endpoint. |
| `.gitignore` | Added `node_modules/`, `orchid/interfaces/web_ui/node_modules/`, `orchid/interfaces/portal/node_modules/`, `orchid/interfaces/web_ui/dist/`, `orchid/interfaces/portal/dist/`. |

### Do not touch without reason

- `orchid/web/server.py` — dead-end file, never loaded by `orchid serve`. Don't add routes here. See prior HANDOFF for the full history of why this file is a trap.
- `orchid/interfaces/web_ui/` — the existing power-user/admin SPA. Phase 1 deliberately left it untouched. Do not merge portal code into it.
- `orchid/auth/jwt.py` — crypto is settled. Don't touch argon2id params.

---

## 5. Gotchas & Hard-Won Knowledge

**PyJWT and argon2-cffi were missing from the venv.** The `_AUTH_AVAILABLE` flag in `web_server.py` silently stays `False` if these aren't installed, and auth routes simply don't register (all auth endpoints 404). If auth endpoints 404 unexpectedly, check: `python -c "from orchid.interfaces.web_server import _AUTH_AVAILABLE; print(_AUTH_AVAILABLE)"`. Fix: `uv pip install "PyJWT>=2.8.0" argon2-cffi`. Both are in `pyproject.toml` but weren't in the dev venv at session start.

**`store.update_user()` not `store.upsert_user()`.** `FileUserStore` has `update_user()`. `upsert_user()` doesn't exist. Calling the wrong one raises `AttributeError` at runtime. The method names are not intuitive — `update_user` replaces the full user record.

**Portal build base path.** The portal `vite.config.js` has `base: '/app/'`. This means the built `index.html` has `src="/app/assets/index-HASH.js"`. If you change this, all asset loads break. The server mounts assets at `/app/assets` — these must match.

**Cookie-based auth for the portal.** The portal uses the same HttpOnly cookies (`orchid_access`, `orchid_refresh`) as the main app. No token is passed in headers. Fetch calls to `/api/*` from the portal work automatically because they're same-origin. The `credentials: 'include'` flag is NOT needed — same origin.

**`user.role` is `"user"` not `"regular"`.** Role values are `"user"`, `"admin"`, `"readonly"`. The portal user menu shows "Admin Console" link when `user.role === 'admin'`. The redirect check in `web_server.py` excludes `("admin",)`. Don't use `"regular"` anywhere.

**Test fixture shares `_store_instance`.** The `auth_client` fixture in `test_portal_api.py` sets `store_mod._store_instance = new_store` directly. If tests run in the same process without fixture teardown, a leaked singleton can cause cross-test bleed. Each test function gets a fresh `tmp_path` and new store, so it's fine — but be aware of this pattern when adding more auth tests.

**TestClient `cookies=` deprecation warning.** Starlette's `TestClient` warns that passing `cookies=` per-request is deprecated. It still works. To silence: set cookies on the client instance directly (`client.cookies.update(...)`). Non-blocking for now.

---

## 6. Conventions In Play

**Caveman mode active** — harness compresses assistant responses. Doesn't affect code output.

**Commit style:** Conventional Commits. `feat(portal):`, `fix(portal):`, etc. Co-authored line required (harness adds it). Subject ≤ 72 chars.

**Portal component style:** Inline styles everywhere (no CSS modules, no Tailwind). CSS custom properties for tokens (`var(--accent)`, `var(--surface)`, etc.). All defined in `src/index.css`. Match `web_ui`'s design tokens exactly — same hex values, same variable names.

**No React Router in the portal.** Navigation is `useState` view switching. Two views currently: `dashboard` and `settings`. If it grows to 5+ views, consider adding a router, but not yet.

**Portal API calls go to `/api/*` with no auth header** — cookie auth is automatic (same origin). All fetch calls in hooks use plain `fetch('/api/...')`.

**Tests for `orchid.interfaces.web_server` use the `app_client` fixture from `test_web_v2.py` as the pattern.** It creates a fresh `create_app([])`, resets module state, does NOT mock the auth layer (real store). See `test_portal_api.py` for the auth-enabled variant.

**`dist/` is gitignored** — portal must be built before deployment. There is no CI build step yet. Manual: `cd orchid/interfaces/portal && npm install && npm run build`.

**Phase proposal doc is ground truth.** `docs/multiuser-proposal.md` has the resolved decisions table, phase breakdown, and spec for upcoming phases. Don't re-debate what's in there.

---

## 7. Open Questions

1. **Phase 2 scope: what notification channels to implement first?** User said email + Telegram + Slack in Phase 2. All three, or just email first with the others in Phase 3? The existing Telegram and Slack bots (`orchid serve --telegram/--slack`) are already wired — per-user notification routing is a new concern.

2. **Credential encryption key derivation.** Proposal says `Fernet key = HMAC(JWT_SECRET, user_id)`. Rotating `JWT_SECRET` would invalidate all credential vaults. Should the vault use a separate `ORCHID_VAULT_KEY` env var instead? Need user decision before implementing.

3. **Portal build step in deployment.** Currently manual. Should `orchid serve` auto-build the portal if `node` is available and `dist/` is stale? Or add a Makefile target? Or document it in the README?

4. **Phase 2 admin-invite flow spec.** User chose admin-invite only registration. The UX is: admin creates user account → user gets a link/token → user sets password on first login. The "set password on first login" flow isn't specced yet. Need to know: email invite link (requires SMTP), or admin-generated one-time token the user enters manually?

5. **Should readonly-role users be redirected to `/app/` too?** Currently the redirect is for `user.role not in ("admin",)`, which includes `readonly`. Is that the right behavior, or should readonly users go somewhere else?

---

## 8. Do Not Touch

- **`orchid/web/server.py`** — dead-end file, never loaded by `orchid serve`. See §5 above for full history. Do not add features here.
- **`orchid/interfaces/web_ui/`** — existing power-user SPA. Phase 1 left it untouched deliberately. Don't merge portal components into it.
- **`orchid/auth/jwt.py`** — crypto params settled. No changes without security review.
- **`docs/multiuser-proposal.md` decisions section** — all open questions were resolved with the user this session. Don't re-debate Postgres timeline, team model, MCP credential sharing, notification channels, or registration model.
- **`.claude/settings.local.json`** — local harness config, always modified, never commit.
- **`orchid/interfaces/portal/vite.config.js` `base: '/app/'`** — load-bearing. Changing this breaks the portal build.

---

## 9. Resume Command

> Read `HANDOFF.md`. We're building multi-user support for Orchid (internal agentic OS). Phase 1 (user portal SPA at `/app`) is complete at commit `14b775a` — 44 tests pass. Start Phase 2: credential vault + per-user notification config. Spec is in `docs/multiuser-proposal.md` under "Phase 2". Before writing code, ask: (1) Should credential encryption use a separate `ORCHID_VAULT_KEY` env var, or derive from `JWT_SECRET + user_id`? (2) For admin-invite flow, is the first-login token delivered via email link or manually? Do not touch `orchid/web/server.py` (dead-end file). Do not change `vite.config.js` `base: '/app/'`. Commit between major changes.
