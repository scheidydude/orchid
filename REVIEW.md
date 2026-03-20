# Orchid — Code & Application Review

**Date:** 2026-03-19
**Reviewed by:** Claude Code (automated review)
**Project:** AI Agent Orchestration Framework
**Tests:** 267 passing

---

## 1. Project Overview

Orchid is a **self-hosted AI agent orchestration framework** for autonomous software development. Installed globally and pointed at external project directories. The framework:

- Manages software projects via a **ReAct-loop agent system** with specialized roles (developer, researcher, reviewer, delegator)
- Supports **pluggable AI backends**: Claude API, llama.cpp (local), Ollama, OpenAI, AWS Bedrock
- Uses **file-based state management** via markdown task boards (`tasks.md`) and hot memory (`CLAUDE.md`)
- Provides **multiple interfaces**: CLI, Web UI (React + FastAPI), Telegram bot, Slack bot
- Supports **multi-project parallelism** with process isolation and shared rate limiting
- Uses **SearXNG** (self-hosted) as primary search, falling back to Brave then DuckDuckGo

**Stack:** Python 3.12+, FastAPI, ChromaDB, sentence-transformers, React 18 + Vite, SQLite-free (JSON/JSONL state)

---

## 2. Architecture

```
orchestrator.py         Main task dispatch loop (Reason→Act→Observe)
  ├─ agents/
  │  ├─ base.py          ReAct loop + tool dispatcher
  │  ├─ developer.py     Code generation (enforces file writes)
  │  ├─ researcher.py    Web search + summarization
  │  ├─ reviewer.py      Quality gates (always routes to Claude)
  │  └─ delegator.py     Sub-agent spawning with depth limits
  ├─ providers/
  │  ├─ registry.py      5-layer routing resolution with caching
  │  ├─ anthropic.py     Claude API (tenacity retry on 429/connection errors)
  │  ├─ local.py         llama.cpp OpenAI-compat
  │  ├─ ollama.py        Ollama
  │  ├─ openai.py        OpenAI / OpenRouter
  │  └─ bedrock.py       AWS Bedrock (boto3 lazy import)
  ├─ memory/
  │  ├─ state.py         tasks.md + CLAUDE.md reader/writer + TaskResultStore
  │  ├─ decisions.py     Append-only decision log (JSON Lines)
  │  └─ vector.py        ChromaDB semantic memory (graceful degradation)
  ├─ tools/
  │  ├─ filesystem.py    read_file, write_file, append_file, list_dir
  │  ├─ shell.py         Bash execution (regex blocklist + timeout)
  │  ├─ search.py        SearXNG → Brave → DuckDuckGo with per-query fallback
  │  └─ consistency.py   check_imports() import scanner
  ├─ interfaces/
  │  ├─ cli.py           Typer CLI (fully implemented incl. interactive mode)
  │  ├─ web_server.py    FastAPI + WebSocket streaming + /health endpoint
  │  ├─ telegram_bot.py  Telegram interface
  │  └─ slack_bot.py     Slack Socket Mode
  ├─ multi.py            Multi-project parallelism (semaphored API + LLM calls)
  ├─ discovery.py        Watchdog-based project auto-discovery
  ├─ session.py          Lifecycle: load → compress → save → embed
  └─ config.py           3-layer merge (defaults → project → CLI)
```

---

## 3. Status of Previous Issues (2026-03-18)

All Priority 1–3 issues from the previous review have been resolved:

| # | Issue | Status |
|---|-------|--------|
| P1.1 | Anthropic retry logic (D0035) | ✅ Fixed — tenacity with backoff + jitter |
| P1.2 | `response.content[0]` IndexError guard | ✅ Fixed — raises ProviderError on empty |
| P1.3 | Silent failure in decisions.py parser | ✅ Fixed — logs warning with line number |
| P2.4 | Shell blocklist weak substring matching | ✅ Fixed — 6 compiled regex patterns |
| P2.5 | Local LLM rate limiting in multi mode | ✅ Fixed — semaphore in multi.py |
| P2.6 | Bare `except Exception: return False` | ✅ Fixed — `logger.debug()` added |
| P2.7 | Vector memory diagnostics | ✅ Fixed — `_unavailability_reason` enum |
| P3.8 | `next_task()` O(n) performance | ✅ Acceptable — task counts don't reach scale where this matters |
| P3.10 | Structured error codes | ✅ Fixed — `orchid/errors.py` with hierarchy |
| P4.13 | Search backend availability caching | ✅ Fixed — per-query fallback chain with TTL cache |

**New since last review:**
- Interactive CLI mode fully implemented
- SearXNG deployed at `searxng.scheidy.com` with `/healthz` health check
- `orchid/cli.py` dead stub deleted (was the source of false P1 alert)
- End-to-end integration tests added (9 tests)
- `orchid decide` CLI tested (5 tests)
- `/health` endpoint added to web server
- README and docs updated

---

## 4. New Issues

### Priority 1 — Fix Now

#### 4.1 Empty Response Guard Missing in 3 Providers
**Files:** `orchid/providers/local.py:79`, `orchid/providers/ollama.py:68`, `orchid/providers/openai.py:74`
**Severity:** HIGH

All three providers access `response.choices[0].message.content` with no guard. If the upstream server returns an empty `choices` array (a known edge case under load), this raises `IndexError` which propagates as an unhandled exception and fails the task entirely.

`anthropic.py` already has the correct pattern — copy it:

```python
# Current (all three):
return response.choices[0].message.content or ""

# Fix:
if not response.choices:
    raise ProviderError(f"{self.name}: empty choices in response")
return response.choices[0].message.content or ""
```

---

#### 4.2 Bedrock Provider Deep Dict Access Without Guards
**File:** `orchid/providers/bedrock.py:108`
**Severity:** HIGH

```python
return response["output"]["message"]["content"][0]["text"]
```

Four levels of unguarded dict/list access. Any structural deviation from the expected Bedrock response format raises `KeyError` or `IndexError` with no useful error message.

```python
# Fix:
try:
    return response["output"]["message"]["content"][0]["text"]
except (KeyError, IndexError) as e:
    raise ProviderError(f"Bedrock: unexpected response structure: {e}") from e
```

---

#### 4.3 TaskResultStore Silently Drops Corrupt Lines
**File:** `orchid/memory/state.py` — `TaskResultStore._read_all()`
**Severity:** MEDIUM

`task_results.json` uses the same JSON Lines format as `decisions.json`, but unlike decisions, corrupt lines raise `JSONDecodeError` unhandled — crashing any operation that reads stored results (rollups, `--get-result`).

The fix pattern already exists in `decisions.py`:

```python
# Fix: wrap the json.loads() call:
try:
    entries.append(json.loads(line))
except json.JSONDecodeError:
    logger.warning("Skipping corrupt line in task_results.json: %.100s", line)
```

---

### Priority 2 — Fix Soon

#### 4.4 Anthropic Retry Only Covers Rate Limits
**File:** `orchid/providers/anthropic.py:58-63`
**Severity:** MEDIUM

The tenacity retry decorator only retries on `RateLimitError`. Transient network failures (`APIConnectionError`, `APITimeoutError`) are common in production and should also be retried:

```python
# Current:
retry=lambda e: isinstance(e, anthropic.RateLimitError)

# Fix:
retry=lambda e: isinstance(e, (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
))
```

---

#### 4.5 `status.value` String Comparison Inconsistency
**Files:** `orchid/interfaces/cli.py:221,373`, `orchid/interfaces/slack_bot.py:218,286`, `orchid/interfaces/slack_formatter.py:70,105`, `orchid/interfaces/telegram_bot.py:210`
**Severity:** LOW

Core code uses `t.status == TaskStatus.TODO` (enum comparison, type-safe). Interface layer uses `t.status.value == "TODO"` (string comparison). Both work but the inconsistency means a renamed enum value would silently break only the interface layer.

Standardise on enum comparison throughout.

---

#### 4.6 Bedrock Raises `ImportError` Instead of `ProviderError`
**File:** `orchid/providers/bedrock.py:74-78`
**Severity:** LOW

All other providers raise `ProviderError` or `ProviderUnavailableError`. Bedrock raises `ImportError` when boto3 is missing. This breaks any code that catches `ProviderError` generically. Change to `ProviderError` with the boto3 install hint in the message.

---

### Priority 3 — Nice to Have

#### 4.7 Test Coverage Gaps
**Severity:** LOW

Current: 267 tests. Gaps:
- No tests for `response.choices` empty/missing in `local.py`, `ollama.py`, `openai.py`
- No tests for `TaskResultStore._read_all()` with a corrupt JSON line
- No tests for Bedrock provider (requires boto3 mock)
- No test for shell blocklist with obfuscated payloads (`bash -c`, command substitution)
- No test for `_maybe_compress_hot_memory()` when hot memory exceeds threshold

---

#### 4.8 Vector Memory Still Uses Word-Boundary Chunking
**File:** `orchid/memory/vector.py:21-34`
**Severity:** LOW

Chunking splits on whitespace, not BPE tokens. A 512-word chunk can exceed the 1024-token embedding limit for technical content (identifiers, code). Current mitigation: embedding failures are caught and logged, so oversized chunks are silently skipped rather than crashing.

This is an acceptable trade-off for now, but a simple improvement would be to cap word count conservatively at 400 (already the default) and document the known gap.

---

#### 4.9 DDG Sponsored Results Not Filtered
**File:** `orchid/tools/search.py:143-163`
**Severity:** LOW

DuckDuckGo HTML results include sponsored entries in `.result__body` without a distinguishing CSS class. These get returned as organic results. Impact is low given SearXNG is now the primary backend and DDG is only used as a last-resort fallback.

---

#### 4.10 No CI Pipeline
**Severity:** LOW

No `pyproject.toml` CI configuration, no GitHub Actions workflow, no pre-commit hooks. The test suite requires `pytest` to be run manually. Risk: regressions go undetected between sessions.

**Minimum viable CI** (GitHub Actions):
```yaml
- run: python -m pytest tests/ -x -q
- run: ruff check orchid/
```

---

## 5. Security

### 5.1 Shell Blocklist — Improved but Still a Blocklist
**File:** `orchid/tools/shell.py`

The previous substring approach has been replaced with 6 compiled regex patterns covering `rm -rf /`, `mkfs`, `dd if=`, fork bombs, shutdown/reboot, and block device writes. This is significantly better.

Remaining concerns:
- Command substitution (`$(rm -rf /)`) is not blocked
- `bash -c '...'` with obfuscated payload is not blocked
- An allowlist approach (only permit `git`, `python`, `node`, `npm`, `pytest`, etc.) would be more robust for the agent's actual use cases

The current approach is reasonable for a trusted single-user environment.

### 5.2 Path Traversal Protection ✅
`orchid/agents/base.py:42-55` — correctly validates all file paths against `project_dir`. Well implemented.

### 5.3 Web UI Has No Authentication
`orchid/interfaces/web_server.py` — no auth on any endpoint. Documented as a single-user design choice. Acceptable for `localhost` use; a problem if exposed via Traefik without additional protection.

### 5.4 Session Logs Contain Full ReAct Traces
`.orchid/session_logs/` contains all agent thought/action/observation cycles which may include sensitive project content. The `.orchid/` directory is correctly gitignored by `orchid init`.

---

## 6. What's Done Well

**Architecture**
- Clean provider abstraction: availability caching, fallback chain, 5-layer routing resolution
- ReAct loop with 4+ action formats (JSON, bracket, heredoc, path) for model compatibility
- Sub-agent context trimming (1000 chars + top-3 recall) prevents trace bloat in delegation
- File-based state — zero databases, trivially inspectable and diffable

**Resilience**
- Vector memory, embeddings, and all providers fail non-fatally
- Search: per-query fallback chain (SearXNG → Brave → DDG) with automatic cache invalidation on failure
- Hot memory auto-compressed when it exceeds threshold
- Process isolation for multi-project runs

**Observability**
- All state transitions logged with context
- Model routing decisions logged with reason and source
- Live `.live.log` (tailable) + structured `.jsonl` (parseable)
- Real-time WebSocket streaming to web UI
- `/health` endpoint for systemd/Traefik probes

**Testing**
- 267 tests, all passing, no external API calls required
- `@pytest.mark.network` for real-network tests
- End-to-end integration tests covering full `Session.load → Orchestrator.run_loop → TaskResultStore` path
- Live SearXNG connectivity tests

**Documentation**
- Comprehensive README with install, quick-start, CLI reference, architecture
- Inline architecture decisions (D0001–D0038) in CLAUDE.md
- `docs/getting-started.md` with worked examples including model routing guidance

---

## 7. Recommended Fix Order

### Priority 1 — Do Now (2–3 hours)
1. Add `response.choices` guard to `local.py`, `ollama.py`, `openai.py` — copy from `anthropic.py`
2. Wrap Bedrock dict access in try/except → raise `ProviderError`
3. Add `JSONDecodeError` handling to `TaskResultStore._read_all()` — copy from `decisions.py`

### Priority 2 — This Week (1–2 hours)
4. Extend Anthropic retry to also cover `APIConnectionError` and `APITimeoutError`
5. Standardise `t.status ==` enum comparison in interface layer (remove `.value` string comparisons)
6. Change Bedrock `ImportError` to `ProviderError`

### Priority 3 — Next Sprint
7. Add tests for empty `response.choices` in all four providers
8. Add test for `TaskResultStore` corrupt line handling
9. Add GitHub Actions CI (pytest + ruff)

### Priority 4 — Backlog
10. Shell tool allowlist approach for known-safe commands
11. Web UI basic auth (configurable, off by default)
12. Systemd service hardening (`PrivateTmp`, `NoNewPrivileges`, `ProtectSystem`)
13. BPE-based token chunking for vector memory

---

## 8. Summary Scorecard

| Aspect | Rating | Notes |
|--------|--------|-------|
| Architecture | ⭐⭐⭐⭐⭐ | Clean, layered, pluggable — genuinely well-designed |
| Code Quality | ⭐⭐⭐⭐ | Good overall; 3 empty-response guards missing in providers |
| Security | ⭐⭐⭐ | Shell blocklist improved; path traversal handled; no web UI auth (by design) |
| Error Handling | ⭐⭐⭐⭐ | Comprehensive; bedrock and 3 providers have unguarded access |
| Performance | ⭐⭐⭐⭐⭐ | Per-query search fallback, semaphored LLM calls, availability caching |
| Testing | ⭐⭐⭐⭐ | 267 tests; edge cases around empty provider responses not covered |
| Documentation | ⭐⭐⭐⭐⭐ | Excellent README, inline decisions, worked examples |
| Feature Completeness | ⭐⭐⭐⭐⭐ | All planned tasks done; SearXNG live; interactive mode working |

---

## 9. Conclusion

Orchid is **production-ready**. All Priority 1–3 issues from the previous review have been resolved. The codebase has improved significantly: better error handling, a structured error hierarchy, hardened shell blocklist, local LLM rate limiting, per-query search fallback, and substantially more test coverage (227 → 267 tests).

**Three new Priority 1 items** remain — all are straightforward copy-paste fixes from patterns already present in the codebase:
- Add `response.choices` guards to `local.py`, `ollama.py`, `openai.py` (copy from `anthropic.py`)
- Guard Bedrock dict access with try/except
- Add JSON error handling to `TaskResultStore._read_all()` (copy from `decisions.py`)

These are the only things standing between "works reliably in normal use" and "works reliably when providers misbehave".
