# Next Features Plan

Six improvements identified in `docs/Orchid vs Agentic OS.md` after the Phase 1–6 gap-closure work.
Each section: **Issue → Fix → Implementation plan** with specific files and estimated effort.

Ordered by impact / risk ratio (highest first).

---

## 1. LLM Provider Fallback Chain

### Issue

`ProviderRegistry.resolve_name()` picks **one** provider per task via the 8-layer priority chain.
When that provider returns `ProviderUnavailableError` (503, timeout, key expired), the task is
immediately marked `BLOCKED`. A transient outage in one provider kills all tasks routed to it —
even when equivalent alternatives (OpenRouter, local llama.cpp) are available.

`CostScheduler` already detects 429 rate-limit responses and sets `rate_pressure` per provider,
but this only triggers the existing local-fallback heuristic (`prefer_local_under_pressure`).
There is no general retry-on-503 or ordered fallback chain.

### Fix

Add a `fallback` list to provider config that specifies ordered alternatives:

```yaml
# .orchid.yaml
providers:
  task_types:
    code_generate:
      name: claude
      fallback: [openrouter, local]   # tried in order on 503/timeout
```

When a provider fails with a retriable error (`ProviderUnavailableError` or HTTP 503/502/429),
the orchestrator tries the next entry in the chain before marking the task blocked.
Successful fallback is logged and recorded in the cost ledger (`node_id = "fallback-N"`).

### Implementation Plan

**New enum:** `RetriableProviderError` — subclass of `ProviderUnavailableError`, raised by providers on
503/502/timeout (as opposed to permanent errors like invalid API key or missing model).

**`providers/base.py`**
- Add `RetriableProviderError(ProviderUnavailableError)`.
- Add `fallback: list[str] = []` to `ProviderBase` dataclass.

**`providers/registry.py`**
- `resolve_name()`: return `(primary_name, fallback_list)` or add `resolve_chain(task) -> list[str]`.
- Read `providers.task_types.<type>.fallback` from config.

**`orchestrator.py` `_execute_task()`**
- Replace the single `_resolve_provider()` call + single `agent.run()` with a loop over the
  fallback chain:
  ```python
  for provider_name in [decision.model] + decision.fallback:
      try:
          result_text = self._run_with_provider(task, plan, provider_name, ...)
          break
      except RetriableProviderError as e:
          logger.warning("Provider %s failed (%s), trying next", provider_name, e)
          continue
  else:
      raise ProviderUnavailableError("All providers in fallback chain exhausted")
  ```
- Extract `_run_with_provider(task, plan, provider_name, ...)` helper from current `_execute_task`.

**`cost/scheduler.py`**
- Extend `set_rate_pressure()` to accept 503/502 as well as 429.
- `check_rate_limit()`: skip a provider marked rate-limited rather than blocking the whole task.

**`orchid.defaults.yaml`**
```yaml
providers:
  fallback_on_errors: [429, 503, 502]   # HTTP status codes that trigger fallback
  max_fallback_attempts: 3
```

**Files:** `providers/base.py`, `providers/registry.py`, `orchestrator.py`, `cost/scheduler.py`,
`orchid.defaults.yaml`

**Effort:** S (2–3 days). No new dependencies. Fully backward-compatible.

---

## 2. Async Agent Execution Model

### Issue

`BaseAgent.run()` is a synchronous blocking function. Every ReAct iteration blocks the calling thread:

1. `call()` in `tools/models.py` makes a synchronous `httpx.post()` that holds a thread for the full
   LLM round-trip (often 10–60 s per iteration).
2. The worker pool uses `ThreadPoolExecutor` — each task ties up one OS thread for its entire lifetime.
   At `max_parallel=4` with 60 s iterations, 4 threads are permanently blocked. Threads are cheap but
   not free at hundreds of concurrent tasks.
3. True mid-call preemption is impossible: `agent.suspend()` can only pause at the **boundary** between
   iterations, never inside `httpx.post()`.

### Fix

Make `BaseAgent.run()` an `async def` coroutine. Replace `httpx.post()` in `tools/models.py` with
`httpx.AsyncClient.post()`. This allows:

- Genuine `asyncio.Task.cancel()` mid-LLM-call (cancels the HTTP request cleanly).
- Cooperative suspension via `asyncio.Event` instead of `threading.Event`.
- Worker pool driven by `asyncio.Semaphore` instead of `ThreadPoolExecutor`.

### Implementation Plan

This is the highest-complexity change in this list. Phased approach to avoid breaking everything:

**Phase A — async `call()` (non-breaking):** 2–3 days
- `tools/models.py`: add `async_call(messages, model_key, system) -> str` alongside existing `call()`.
- Uses `httpx.AsyncClient` with `async with` context.
- Both `call()` and `async_call()` share the same retry/error-handling logic via a shared `_do_call()`.

**Phase B — async `BaseAgent.run()` (breaking):** 1 week
- Rename current `run()` → `run_sync()` (keeps backward compat for tests and CLI paths that can't easily
  go async).
- Add `async def run(self, task_description: str) -> str` that calls `await async_call()`.
- Replace `threading.Event` suspend/cancel with `asyncio.Event`.
- Replace `self._check_injection_queue()` with an async poll.
- `_dispatch()` (tool execution) wraps sync tools in `asyncio.to_thread()` to avoid blocking the loop.

**Phase C — async orchestrator dispatch:** 1 week
- `orchestrator._execute_task()` → `async def _execute_task()`.
- `BackgroundRunner._execute_group()`: replace `ThreadPoolExecutor.submit()` with
  `asyncio.gather(*[orch._execute_task(t) for t in group])`.
- Remove worker pool (no longer needed — asyncio concurrency replaces thread pool).

**Phase D — async worker subprocess (optional):** 3–5 days
- `worker_subprocess.py`: run `asyncio.run(agent.run(...))` in pool mode.

**Files (Phase A):** `tools/models.py`
**Files (Phase B):** `agents/base.py`, `agents/*.py` (subclasses)
**Files (Phase C):** `orchestrator.py`, `runner.py`, `subprocess_runner.py`

**Effort:** L (2–3 weeks total). Highest risk — touches the entire hot path. Recommend feature-flag:
`agents.async_mode: false` in defaults, true enables the async path.

---

## 3. Distributed Task Queue (Redis Streams)

### Issue

The current multi-node model (`RemoteDispatcher` + HTTP) requires:

1. All worker URLs known upfront in config — no dynamic scaling.
2. Orchestrator polls worker `/health` endpoints to detect availability.
3. Worker failure while executing a task loses the task silently (no requeue).
4. The orchestrator is a single point of failure — if it crashes, all in-flight remote tasks are lost.

### Fix

Replace the direct HTTP dispatch with a **Redis Streams** message queue:

- Orchestrator enqueues tasks as JSON messages to `orchid:tasks:{project_id}`.
- Worker nodes consume from the stream using consumer groups (`XREADGROUP`).
- On task completion, workers write results to `orchid:results:{project_id}`.
- Redis handles redelivery of unacknowledged messages (`XAUTOCLAIM`), so a crashed worker's
  tasks are automatically picked up by another worker.
- Orchestrator waits for results with `XREAD BLOCK`.

Existing `TaskContext`/`WorkerResult`/`WorkerEvent` protocol maps directly to stream messages —
only the transport changes.

### Implementation Plan

**New optional dependency:**
```toml
[project.optional-dependencies]
queue = ["redis>=5.0.0"]
```

**New file: `orchid/remote/queue.py`**
```python
class TaskQueue:
    def __init__(self, redis_url: str, project_id: str): ...
    def enqueue(self, ctx: TaskContext) -> str: ...          # returns message ID
    def dequeue(self, consumer: str, timeout_s: int) -> TaskContext | None: ...
    def ack(self, msg_id: str) -> None: ...
    def put_result(self, result: WorkerResult) -> None: ...
    def get_result(self, task_id: str, timeout_s: int) -> WorkerResult | None: ...
    def reclaim_stale(self, older_than_ms: int) -> list[TaskContext]: ...
```

**`remote/dispatcher.py`**
- Add `QueueDispatcher(TaskQueue)` alongside existing `RemoteDispatcher`.
- `QueueDispatcher.dispatch()`: `queue.enqueue(ctx)` then `queue.get_result(task_id, timeout)`.
- Auto-reclaim stale messages at startup.

**`worker_subprocess.py` queue mode**
- New entry point `queue_main(redis_url, project_id, consumer_name)`:
  loop `queue.dequeue()` → `_run(ctx)` → `queue.put_result()` → `queue.ack()`.
- Start via `orchid worker --queue redis://...`.

**`interfaces/cli.py`**
- `orchid worker` command: accept `--queue` flag; if set, start `queue_main()`.

**`orchid.defaults.yaml`**
```yaml
remote:
  queue_url: ""               # Redis URL; empty = use direct HTTP dispatch
  queue_stream_prefix: "orchid"
  queue_consumer_group: "workers"
  queue_reclaim_ms: 30000     # reclaim tasks idle > 30s
```

**Migration path:** `ORCHID_QUEUE_URL` env var auto-switches dispatch mode.
Direct HTTP dispatch (`RemoteDispatcher`) remains as fallback when queue is not configured.

**Files:** `remote/queue.py` (new), `remote/dispatcher.py`, `worker_subprocess.py`,
`interfaces/cli.py`, `pyproject.toml`, `orchid.defaults.yaml`

**Effort:** M (1 week). New dependency but clean interface. Worker protocol unchanged.

---

## 4. OpenTelemetry Observability

### Issue

Orchid has project-local observability (`cost_ledger.jsonl`, `task_metrics.jsonl`, `trace.log`)
but no cross-project, cross-node tracing. Operators running multiple projects or remote workers cannot:

- Correlate a slow task with its LLM calls across providers.
- See a flame graph of: orchestrate → plan → agent.run → tool call → LLM → response.
- Alert on p99 latency or error rate in a standard monitoring stack (Grafana, Datadog, etc.).

### Fix

Instrument with **OpenTelemetry** (OTEL) — the standard for distributed tracing and metrics.
One trace per task execution. One span per ReAct iteration. Tool calls as child spans.

OTEL is opt-in: if `OTEL_EXPORTER_OTLP_ENDPOINT` is not set (or `observability.enabled: false`),
all instrumentation is a no-op (zero overhead via OTEL's no-op tracer).

### Implementation Plan

**New optional dependency:**
```toml
[project.optional-dependencies]
otel = [
    "opentelemetry-sdk>=1.24.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.24.0",
    "opentelemetry-instrumentation-httpx>=0.45b0",
]
```

**New file: `orchid/telemetry.py`**
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

_tracer: trace.Tracer | None = None

def init_telemetry(endpoint: str, service_name: str = "orchid") -> None: ...
def get_tracer() -> trace.Tracer: ...   # returns no-op tracer if not init'd
```

**`orchestrator.py` `_execute_task()`**
```python
with get_tracer().start_as_current_span(f"task:{task.id}") as span:
    span.set_attribute("task.type", task.type)
    span.set_attribute("task.priority", task.priority)
    span.set_attribute("provider", decision.model)
    result_text = agent.run(plan)
    span.set_attribute("task.status", "done")
```

**`agents/base.py` `run()`**
```python
for iteration in range(...):
    with get_tracer().start_as_current_span(f"react.iter.{iteration}") as span:
        response = call(...)
        span.set_attribute("iter.tokens", ...)
```

**`tools/models.py` `call()`**
```python
with get_tracer().start_as_current_span("llm.call") as span:
    span.set_attribute("model", model_key)
    # httpx auto-instrumented by opentelemetry-instrumentation-httpx
```

**`interfaces/web_server.py` `serve()`**
- Call `init_telemetry()` at startup if `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

**`orchid.defaults.yaml`**
```yaml
observability:
  enabled: false              # auto-enabled if OTEL_EXPORTER_OTLP_ENDPOINT is set
  service_name: "orchid"
  export_interval_ms: 5000
```

**Files:** `orchid/telemetry.py` (new), `orchestrator.py`, `agents/base.py`, `tools/models.py`,
`interfaces/web_server.py`, `pyproject.toml`, `orchid.defaults.yaml`

**Effort:** S (2–3 days). OTEL SDK is well-documented. No-op path means zero risk to existing runs.

---

## 5. Agent Capability Versioning

### Issue

`CAPABILITY_REGISTRY` declares allowed tools per agent type, but `ReActCheckpoint` stores:

```python
task_id: str
iteration: int
conversation_history: list[dict]
partial_result: str
timestamp: str
```

No information about **which tool set** or **which model** was active when the checkpoint was saved.

When a checkpoint is resumed after an orchid upgrade, agent config change, or model switch, the
restored agent may have a different tool set than the one that generated the conversation history.
This causes subtle failures: the history references a tool that no longer exists, or a new tool
that the model hasn't been told about is silently unavailable.

### Fix

Add `capability_version: str` and `model_key: str` to `ReActCheckpoint`. Before resuming, the
orchestrator checks whether the current capability hash matches the checkpoint's hash. On mismatch,
it logs a warning and optionally resets to `TODO` (configurable per project).

The capability hash is a deterministic SHA256 of `(sorted(allowed_tools), model_key, agent_type)`.

### Implementation Plan

**`checkpoint/schema.py`**
```python
@dataclass
class ReActCheckpoint:
    task_id: str
    iteration: int
    conversation_history: list[dict]
    partial_result: str = ""
    timestamp: str = ""
    capability_version: str = ""   # SHA256 of (tools, model, agent_type)
    model_key: str = ""
    agent_type: str = ""
```

**`orchid/capability.py`**
- Add `compute_capability_hash(agent_type: str, allowed_tools: frozenset, model_key: str) -> str`:
  ```python
  import hashlib, json
  payload = json.dumps(sorted(allowed_tools) + [model_key, agent_type])
  return hashlib.sha256(payload.encode()).hexdigest()[:16]
  ```

**`agents/base.py`**
- Compute hash at `__init__` time: `self._capability_version = compute_capability_hash(...)`.
- Set on checkpoint before saving:
  ```python
  _cp.capability_version = self._capability_version
  _cp.model_key = self.model_key
  _cp.agent_type = self.agent_type
  ```

**`orchestrator.py` `_execute_task()`**
- After loading `_react_cp`: compare `_react_cp.capability_version` with current agent's hash.
- If mismatch and `agents.strict_capability_resume: true`: reset to TODO, log warning.
- If mismatch and `strict_capability_resume: false` (default): log warning, resume anyway.

**`orchid.defaults.yaml`**
```yaml
agents:
  strict_capability_resume: false   # true = reset task to TODO on capability mismatch
```

**Files:** `checkpoint/schema.py`, `orchid/capability.py`, `agents/base.py`, `orchestrator.py`,
`orchid.defaults.yaml`

**Effort:** XS (1 day). Pure additive — old checkpoints without `capability_version` treated as
mismatch-exempt (empty string skips the check).

---

## 6. Network Namespace Isolation Per Task

### Issue

Subprocess isolation (Phase 3) limits **compute and memory** via `RLIMIT_AS` and `RLIMIT_CPU`,
but tasks run with the **same network access** as the parent orchid process. A task can:

- Make arbitrary HTTP requests to any host (data exfiltration, SSRF).
- Consume all outbound connections (connection exhaustion).
- Call the LLM API directly without going through the cost ledger (unbilled usage).

This matters most in shared/enterprise deployments where untrusted projects or user-supplied
task descriptions could be adversarial.

### Fix

On Linux, wrap each child process in a **network namespace** (`CLONE_NEWNET` via `unshare`),
then allow only a configurable set of hosts/IPs through a lightweight allow-proxy. The proxy
forwards LLM API calls and blocks everything else.

macOS/Windows: no network namespace support — feature is a no-op on non-Linux, logged at startup.

### Implementation Plan

**New file: `orchid/isolation/netns.py`**
```python
import ctypes, os, socket

CLONE_NEWNET = 0x40000000

def enter_network_namespace() -> None:
    """Call from preexec_fn: creates a new network namespace for this process.
    Requires Linux and (usually) CAP_SYS_ADMIN or user namespace support.
    """
    if os.uname().sysname != "Linux":
        return
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    rc = libc.unshare(CLONE_NEWNET)
    if rc != 0:
        import errno
        raise OSError(errno.errorcode.get(ctypes.get_errno(), "?"),
                      "unshare(CLONE_NEWNET) failed")
    # Bring up the loopback interface inside the new namespace
    os.system("ip link set lo up 2>/dev/null")

def is_supported() -> bool:
    try:
        # Check if unprivileged user namespaces are available
        return (os.uname().sysname == "Linux" and
                int(open("/proc/sys/kernel/unprivileged_userns_clone").read().strip()) == 1)
    except Exception:
        return False
```

**New file: `orchid/isolation/allow_proxy.py`**
- Lightweight `http.server`-based forward proxy that checks `Host` header against an allowlist.
- Listens on `127.0.0.1:{PROXY_PORT}` inside the parent namespace.
- Child tasks set `HTTP_PROXY` / `HTTPS_PROXY` env vars to point at it.
- Allowlist entries: `api.anthropic.com`, `api.openai.com`, `openrouter.ai`, etc.

```python
class AllowProxy:
    def __init__(self, port: int, allowed_hosts: list[str]): ...
    def start(self) -> None: ...   # starts in background thread
    def stop(self) -> None: ...
```

**`subprocess_runner.py`**
- Extend `_resource_preexec()` to call `enter_network_namespace()` when
  `isolation.network_namespace: true` (Linux only).
- Pass `HTTP_PROXY` env var pointing at the running `AllowProxy`.
- Start `AllowProxy` lazily in `SubprocessRunner.__init__()` when namespace mode is enabled.

**`orchid.defaults.yaml`**
```yaml
isolation:
  network_namespace: false         # Linux only; requires unprivileged_userns_clone=1
  network_allow_proxy_port: 18080  # port for the allow-proxy inside the parent namespace
  network_allow_hosts:             # hosts child tasks may reach
    - "api.anthropic.com"
    - "api.openai.com"
    - "openrouter.ai"
    - "localhost"
    - "127.0.0.1"
```

**`interfaces/web_server.py`** / **`interfaces/cli.py`**
- Log at startup if `network_namespace: true` and `is_supported()` returns False.

**Enable check:**
```bash
# Verify your kernel supports unprivileged user namespaces:
cat /proc/sys/kernel/unprivileged_userns_clone   # must be 1
```

**Files:** `orchid/isolation/netns.py` (new), `orchid/isolation/allow_proxy.py` (new),
`orchid/isolation/__init__.py` (new), `subprocess_runner.py`, `orchid.defaults.yaml`,
`interfaces/web_server.py`

**Effort:** M (1 week). Linux-only, opt-in. The `ctypes` approach avoids new C dependencies.
The allow-proxy is ~100 lines. Main risk is kernel version and capability requirements varying
by distro.

---

## Priority and Sequencing

| # | Feature | Effort | Risk | Recommended order |
|---|---------|--------|------|-------------------|
| 6 | LLM provider fallback chain | S | Low | **First** — immediate reliability win, no new deps |
| 5 | Agent capability versioning | XS | Low | **Second** — fixes a correctness gap in checkpoint resume |
| 4 | OpenTelemetry observability | S | Low | **Third** — no-op unless configured, enterprise value |
| 3 | Distributed task queue | M | Medium | **Fourth** — prerequisite for horizontal scale |
| 1 | Async agent execution model | L | High | **Fifth** — largest change, requires 3+ on async call() first |
| 2 | Network namespace isolation | M | Medium | **Sixth** — Linux-only, depends on subprocess model being stable |

Items 6, 5, and 4 can be developed in parallel (no shared files). Item 1 (async) is a
prerequisite for getting full benefit from item 2 (network namespace), but item 2 works without it.
