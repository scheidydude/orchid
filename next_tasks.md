# Next Tasks — Gap Closure Sprint

Source: `Orchid-v-Claude-Code-Gap_analysis.md`  
Scope: Hook system · MCP adapter layer · Stream-JSON output · Session checkpointing  
Format: Orchid `tasks.md` convention. Tasks start at T092.

---

## Feature 1: Hook System

Close gap: *Claude Code has 20+ lifecycle events, 4 hook types. Orchid has none.*  
Goal: fire hooks at PreToolUse, PostToolUse, TaskStart, TaskComplete, SessionStart, SessionStop, PhaseTransition. Support blocking and non-blocking hooks. Load from `.orchid.yaml` and per-project `hooks.py`.

### Code Generation

- [ ] **T092** Design and implement `orchid/hooks/registry.py`: `HookRegistry` class with `register(event, handler, blocking=False)`, `fire(event, context) -> HookResult`. `HookResult` carries `blocked: bool`, `mutated_context: dict | None`, `error: str | None`. Blocking hooks that return `blocked=True` halt the caller. Non-blocking hooks run in a thread pool and their results are logged but not awaited by the caller. `type:code_generate` `p1`

- [ ] **T093** Define hook event constants in `orchid/hooks/events.py`: `PRE_TOOL_USE`, `POST_TOOL_USE`, `TASK_START`, `TASK_COMPLETE`, `TASK_FAIL`, `SESSION_START`, `SESSION_END`, `PHASE_TRANSITION`, `DELEGATION_START`, `DELEGATION_END`. Each event has a typed `HookContext` dataclass with relevant fields (task_id, tool_name, action_input, result, phase, etc.). `type:code_generate` `p1` `needs:T092`

- [ ] **T094** Implement hook loader `orchid/hooks/loader.py`: reads `hooks:` section from `.orchid.yaml`. Supports two hook kinds: (1) `shell` — runs a bash command, receives context as JSON on stdin, reads mutated context or `{"block": true}` from stdout. (2) `http` — POSTs context JSON to a URL, reads response. Also loads `hooks.py` from project root if present and registers any functions decorated with `@orchid_hook(event)`. `type:code_generate` `p1` `needs:T093`

- [ ] **T095** Wire hooks into agent ReAct loop `orchid/agent.py`: fire `PRE_TOOL_USE` before executing any Action, pass `{tool, input}` as context. If hook returns `blocked=True`, skip tool execution and inject a synthetic Observation: `"[BLOCKED by hook: <reason>]"`. Fire `POST_TOOL_USE` after each Observation with `{tool, input, output}`. `type:code_generate` `p1` `needs:T094`

- [ ] **T096** Wire hooks into task lifecycle `orchid/orchestrator.py`: fire `TASK_START` before a task begins execution with `{task_id, task_title, task_type}`. Fire `TASK_COMPLETE` on success with `{task_id, duration_s, iterations}`. Fire `TASK_FAIL` on exception with `{task_id, error}`. `type:code_generate` `p1` `needs:T094`

- [ ] **T097** Wire hooks into session and phase transitions: fire `SESSION_START` in `orchid/runner.py` at run start with `{project, mode, provider}`. Fire `SESSION_END` at run end with `{task_count, duration_s}`. Fire `PHASE_TRANSITION` in `orchid/lifecycle.py` when phase advances with `{from_phase, to_phase, project}`. `type:code_generate` `p1` `needs:T094`

- [ ] **T098** Add hook config schema to `.orchid.yaml` and `orchid/config.py`: extend `OrchidConfig` to parse `hooks:` list. Each entry: `{event, kind, command|url, blocking, timeout_s}`. Add `hooks: []` with examples to `orchid/defaults/orchid.defaults.yaml`. `type:code_generate` `p1` `needs:T094`

- [ ] **T099** Add CLI: `orchid hooks list --project .` prints all registered hooks (event, kind, blocking, source). `orchid hooks test <event> --project .` fires the event with a synthetic context and prints the result. `type:code_generate` `p2` `needs:T098`

### Review

- [ ] **T100** Review hook registry and loader implementation: verify blocking hooks cannot deadlock the orchestrator, shell hooks are sandboxed by the existing shell allowlist, http hooks respect timeout, and hook errors are logged but never crash the agent loop. Check `loader.py` for arbitrary code injection via `hooks.py` imports. `type:review` `p1` `needs:T094,T095,T096,T097`

- [ ] **T101** Review hook integration points in `agent.py` and `orchestrator.py`: confirm context objects do not leak secrets (API keys, full file contents), PRE_TOOL_USE context is immutable by default, and mutation is opt-in only for designated mutable fields. `type:review` `p1` `needs:T095,T096`

### Testing

- [ ] **T102** Unit tests `tests/test_hooks_registry.py`: test register/fire blocking hook blocks caller, non-blocking hook runs async and does not block, multiple hooks for same event fire in registration order, exception in hook does not propagate to caller. `type:test_write` `p1` `needs:T092,T093`

- [ ] **T103** Unit tests `tests/test_hooks_loader.py`: test shell hook fires and reads stdout JSON, shell hook `{"block": true}` propagates blocked flag, http hook POSTs correct payload, invalid hooks.py import raises `HookLoadError` not crash. `type:test_write` `p1` `needs:T094`

- [ ] **T104** Integration tests `tests/test_hooks_integration.py`: run a minimal agent session with a shell hook on `PRE_TOOL_USE` that blocks `bash` actions — verify agent receives blocked observation and does not execute bash. Run session with `TASK_COMPLETE` hook that appends to a log file — verify file is written after task completes. `type:test_write` `p1` `needs:T095,T096,T097`

---

## Feature 2: MCP Adapter Layer

Close gap: *Claude Code supports hundreds of MCP servers. Orchid has no MCP support.*  
Design decisions (resolved here, not left to agent):
- All clients are **synchronous** (`subprocess.Popen` + `httpx.Client`) to match Orchid's sync architecture.
- Tool names namespaced `mcp__<server_name>__<tool_name>`.
- Tools injected into `BaseAgent` via existing `register_tool(name, fn)` at line 332 of `orchid/agents/base.py`.
- Config read from `orchid/config.py`'s dict-based `get()` system — no new dataclass needed.
- MCP wiring entry point: `orchid/runner.py` `BackgroundRunner._run()` and `orchid/interfaces/cli.py` `_cmd_auto()`.

### Code Generation

- [ ] **T105** Create `orchid/mcp/types.py`. Also create empty `orchid/mcp/__init__.py` with content `# MCP adapter layer`. Define exactly these three dataclasses and nothing else:
  ```python
  from dataclasses import dataclass, field

  @dataclass
  class MCPToolParam:
      name: str
      type: str        # one of: "string" "integer" "boolean" "object" "array"
      description: str
      required: bool = True

  @dataclass
  class MCPTool:
      name: str
      description: str
      params: list["MCPToolParam"] = field(default_factory=list)

  @dataclass
  class MCPResult:
      content: str
      is_error: bool = False
  ```
  `type:code_generate` `p1`

- [ ] **T106** Create `orchid/mcp/client.py`. Define exactly one ABC and one exception class:
  ```python
  from abc import ABC, abstractmethod
  from typing import Any
  from orchid.mcp.types import MCPTool, MCPResult

  class MCPClientError(Exception):
      pass

  class MCPClient(ABC):
      @abstractmethod
      def connect(self) -> None: ...

      @abstractmethod
      def disconnect(self) -> None: ...

      @abstractmethod
      def list_tools(self) -> list[MCPTool]: ...

      @abstractmethod
      def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult: ...
  ```
  `type:code_generate` `p1` `needs:T105`

- [ ] **T107** Create `orchid/mcp/stdio_client.py`. Implement exactly this class using `subprocess.Popen`:
  ```python
  import json, subprocess, time
  from typing import Any
  from orchid.mcp.client import MCPClient, MCPClientError
  from orchid.mcp.types import MCPTool, MCPToolParam, MCPResult

  class StdioMCPClient(MCPClient):
      def __init__(self, command: str, args: list[str],
                   env: dict[str, str] | None = None, timeout: float = 30.0): ...
      # Stores: self._command, self._args, self._env, self._timeout,
      #         self._process (subprocess.Popen | None), self._req_id (int = 0)

      def connect(self) -> None: ...
      # subprocess.Popen([self._command, *self._args], env={**os.environ, **(self._env or {})},
      #   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
      # Sends initialize JSON-RPC: {"jsonrpc":"2.0","method":"initialize","id":0,
      #   "params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"orchid","version":"1.0"}}}
      # Reads response via _recv(). Raises MCPClientError if process fails to start.

      def disconnect(self) -> None: ...
      # Sends {"jsonrpc":"2.0","method":"shutdown","id":<n>,"params":{}}, reads response.
      # Calls self._process.terminate(), waits up to 5s with self._process.wait(timeout=5),
      # then self._process.kill() if still alive. Sets self._process = None.

      def list_tools(self) -> list[MCPTool]: ...
      # Calls _send("tools/list", {}). Parses result["tools"] list.
      # Each tool dict has "name", "description", optionally "inputSchema".
      # Returns list[MCPTool]. If inputSchema has "properties", parse into MCPToolParam list.

      def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult: ...
      # Calls _send("tools/call", {"name": name, "arguments": arguments}).
      # If result has "isError": True -> return MCPResult(content=str(result.get("content","")), is_error=True)
      # Else -> return MCPResult(content=str(result.get("content", "")))

      def _send(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...
      # self._req_id += 1. Builds msg = {"jsonrpc":"2.0","method":method,"id":self._req_id,"params":params}.
      # Writes json.dumps(msg).encode() + b"\n" to self._process.stdin. Flushes.
      # Calls _recv(). If response has "error" key -> raise MCPClientError(response["error"]["message"]).
      # Returns response["result"].

      def _recv(self) -> dict[str, Any]: ...
      # Sets self._process.stdout timeout via select or reads with deadline using time.time().
      # Reads one line from self._process.stdout. If empty -> raise MCPClientError("server exited").
      # Returns json.loads(line).
      # Implement timeout: use select.select([self._process.stdout], [], [], self._timeout)
      # before reading. If no data -> raise MCPClientError("timeout waiting for server response").
  ```
  `type:code_generate` `p1` `needs:T106`

- [ ] **T108** Create `orchid/mcp/http_client.py`. Implement exactly this class using `httpx.Client` (sync):
  ```python
  import httpx
  from typing import Any
  from orchid.mcp.client import MCPClient, MCPClientError
  from orchid.mcp.types import MCPTool, MCPToolParam, MCPResult

  class HttpMCPClient(MCPClient):
      def __init__(self, url: str, headers: dict[str, str] | None = None,
                   timeout: float = 30.0): ...
      # Stores: self._base_url = url.rstrip("/"), self._headers = headers or {},
      #         self._timeout = timeout, self._client (httpx.Client | None)

      def connect(self) -> None: ...
      # self._client = httpx.Client(follow_redirects=False, timeout=self._timeout,
      #                              headers=self._headers)
      # Sends GET self._base_url + "/health" (ignore 404 — only fail on connection error).
      # Wraps httpx.ConnectError -> MCPClientError("cannot connect to " + self._base_url).

      def disconnect(self) -> None: ...
      # self._client.close(). Sets self._client = None.

      def list_tools(self) -> list[MCPTool]: ...
      # Calls _post("/tools/list", {}). Parses response["tools"] -> list[MCPTool].
      # Same MCPToolParam parsing as StdioMCPClient.list_tools().

      def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult: ...
      # Calls _post("/tools/call", {"name": name, "arguments": arguments}).
      # If response.get("isError") -> MCPResult(content=str(response.get("content","")), is_error=True)
      # Else -> MCPResult(content=str(response.get("content", "")))

      def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...
      # self._client.post(self._base_url + path, json=body).
      # Catches httpx.TimeoutException -> raise MCPClientError("timeout").
      # If response.status_code != 200 -> raise MCPClientError(f"HTTP {response.status_code}: {response.text[:200]}").
      # Returns response.json().
  ```
  `type:code_generate` `p1` `needs:T106`

- [ ] **T109** Create `orchid/mcp/adapter.py`. Implement exactly this class:
  ```python
  from orchid.mcp.client import MCPClient
  from orchid.mcp.types import MCPTool, MCPResult

  _MAX_DESC = 200  # truncate tool descriptions to prevent prompt injection

  class MCPToolAdapter:
      def __init__(self, server_name: str, client: MCPClient,
                   allowed_tools: list[str] | None = None): ...
      # Stores: self._server_name, self._client, self._allowed_tools, self._tools: list[MCPTool] = []

      def load_tools(self) -> None: ...
      # self._tools = self.filter_tools(self._client.list_tools())

      def get_tool_names(self) -> list[str]: ...
      # return [f"mcp__{self._server_name}__{t.name}" for t in self._tools]

      def get_tools_for_prompt(self) -> str: ...
      # return "\n".join(
      #   f"mcp__{self._server_name}__{t.name}: {t.description[:_MAX_DESC]}"
      #   for t in self._tools
      # )

      def is_mcp_action(self, action_name: str) -> bool: ...
      # return action_name.startswith(f"mcp__{self._server_name}__")

      def execute(self, action_name: str, arguments: dict) -> str: ...
      # tool_name = action_name[len(f"mcp__{self._server_name}__"):]
      # result = self._client.call_tool(tool_name, arguments)
      # return f"[ERROR: {result.content}]" if result.is_error else result.content

      def filter_tools(self, tools: list[MCPTool]) -> list[MCPTool]: ...
      # if self._allowed_tools is None: return tools
      # return [t for t in tools if t.name in self._allowed_tools]

      def as_tool_fn(self, tool_name: str):
      # Returns a callable suitable for BaseAgent.register_tool().
      # Signature: def _fn(args: dict) -> str
      # Calls self.execute(f"mcp__{self._server_name}__{tool_name}", args)
      # Catches MCPClientError -> returns f"[ERROR: {e}]"
          ...
  ```
  `type:code_generate` `p1` `needs:T107,T108`

- [ ] **T110** Create `orchid/mcp/manager.py`. Implement exactly this class. It is fully synchronous:
  ```python
  import logging, time
  from orchid.mcp.adapter import MCPToolAdapter
  from orchid.mcp.client import MCPClientError

  logger = logging.getLogger(__name__)

  class MCPManager:
      def __init__(self, project_config: dict): ...
      # project_config is the dict from orchid.config.configure_for_project().
      # self._server_configs: list[dict] = project_config.get("mcp_servers") or []
      # self._adapters: list[MCPToolAdapter] = []

      def start_all(self) -> None: ...
      # For each cfg in self._server_configs where cfg.get("enabled", True) is True:
      #   Build client: if cfg["kind"] == "stdio" -> StdioMCPClient(cfg["command"], cfg.get("args",[]), cfg.get("env"))
      #                 if cfg["kind"] == "http"  -> HttpMCPClient(cfg["url"], cfg.get("headers"))
      #   adapter = MCPToolAdapter(cfg["name"], client, cfg.get("allowed_tools"))
      #   if _start_with_retry(adapter): self._adapters.append(adapter)

      def stop_all(self) -> None: ...
      # For each adapter in self._adapters: adapter._client.disconnect() (catch and log exceptions).
      # self._adapters.clear()

      def get_adapters(self) -> list[MCPToolAdapter]: ...
      # return self._adapters

      def get_tools_prompt_block(self) -> str: ...
      # lines = [a.get_tools_for_prompt() for a in self._adapters if a.get_tool_names()]
      # if not lines: return ""
      # return "\n## MCP Tools\n" + "\n".join(lines) + "\n"

      def execute_mcp_action(self, action_name: str, arguments: dict) -> str | None: ...
      # for adapter in self._adapters:
      #   if adapter.is_mcp_action(action_name): return adapter.execute(action_name, arguments)
      # return None

      def inject_into_agent(self, agent) -> None: ...
      # Calls agent.register_tool(namespaced_name, adapter.as_tool_fn(tool_name))
      # for each adapter in self._adapters, for each tool_name in adapter._tools.
      # Also appends manager.get_tools_prompt_block() to agent.session_context.

      def _start_with_retry(self, adapter: MCPToolAdapter, max_retries: int = 3) -> bool: ...
      # Tries adapter._client.connect() then adapter.load_tools().
      # On MCPClientError: waits 1s, 2s, 4s between retries (time.sleep(2**attempt)).
      # Logs warning on each failure. Returns True on success, False after all retries.
  ```
  `type:code_generate` `p1` `needs:T109`

- [ ] **T111** Extend `orchid/orchid.defaults.yaml` — append MCP servers section. Read the file first to find its end. Append exactly this block at the bottom of the file:
  ```yaml

  # MCP server integrations (Model Context Protocol)
  # Orchid connects to MCP servers and exposes their tools to agents as ReAct actions.
  # Tool names are namespaced: mcp__<name>__<tool_name>
  mcp_servers: []
  # Example stdio server (npx-based):
  # mcp_servers:
  #   - name: github
  #     kind: stdio
  #     command: npx
  #     args: ["-y", "@modelcontextprotocol/server-github"]
  #     env:
  #       GITHUB_PERSONAL_ACCESS_TOKEN: ""
  #     enabled: false
  #     allowed_tools: null    # null = all tools; list restricts e.g. ["create_issue"]
  # Example HTTP server:
  #   - name: myserver
  #     kind: http
  #     url: "http://localhost:8090"
  #     enabled: false
  #     allowed_tools: null
  ```
  `type:code_generate` `p1`

- [ ] **T112** Extend `orchid/config.py` — add one helper function. Read the file first. Append after the last function in the file:
  ```python
  def get_mcp_servers(project_config: dict) -> list[dict]:
      """Return normalised list of mcp_server dicts from a merged project config.

      Fills missing keys with defaults so callers never need to check key existence.
      """
      raw = project_config.get("mcp_servers") or []
      result = []
      for s in raw:
          result.append({
              "name":          s["name"],
              "kind":          s.get("kind", "stdio"),
              "command":       s.get("command", ""),
              "args":          s.get("args", []),
              "env":           s.get("env", {}),
              "url":           s.get("url", ""),
              "headers":       s.get("headers", {}),
              "enabled":       s.get("enabled", True),
              "allowed_tools": s.get("allowed_tools", None),
          })
      return result
  ```
  `type:code_generate` `p1`

- [ ] **T113** Extend `orchid/orchestrator.py` — wire `MCPManager` into task execution. Read the file first. Make exactly two changes:
  1. In `Orchestrator.__init__` (line 115), add parameter `mcp_manager=None` after `trace_enabled: bool = False`. Add `self.mcp_manager = mcp_manager` in the body.
  2. In `Orchestrator._execute_task` (line 192), find where agent is constructed (search for `agent = agent_cls(`). After that agent construction line, add:
     ```python
     if self.mcp_manager:
         self.mcp_manager.inject_into_agent(agent)
     ```
  Add import at top of file (inside the method using TYPE_CHECKING guard to avoid circular import):
  ```python
  # at top of _execute_task, alongside other local imports:
  from orchid.mcp.manager import MCPManager  # noqa: F401 — type hint only
  ```
  `type:code_generate` `p1` `needs:T110`

- [ ] **T114** Extend `orchid/runner.py` — create and teardown `MCPManager` around the run loop. Read the file first. In `BackgroundRunner._run()` (line 70), make exactly these two changes:
  1. After `session.load()` (line 77) and before `orch = Orchestrator(session)` (line 78), add:
     ```python
     from orchid.config import configure_for_project
     from orchid.mcp.manager import MCPManager
     proj_cfg = configure_for_project(project_path)
     mcp_manager = MCPManager(proj_cfg)
     mcp_manager.start_all()
     ```
  2. Change `orch = Orchestrator(session)` to `orch = Orchestrator(session, mcp_manager=mcp_manager)`.
  3. In the `finally:` block (line 97), before `state.current_task = None`, add:
     ```python
     mcp_manager.stop_all()
     ```
  `type:code_generate` `p1` `needs:T113`

- [ ] **T115** Extend `orchid/interfaces/cli.py` — add `mcp` Typer sub-app with two commands. Read the file first. Find the pattern where sub-apps are registered (search for `app.add_typer`). Add a new sub-app with these two commands:
  ```python
  mcp_app = typer.Typer(name="mcp", help="Manage MCP server integrations.")
  app.add_typer(mcp_app)

  @mcp_app.command("list")
  def _cmd_mcp_list(project: str = typer.Option(".", "--project", "-p")):
      """List all configured MCP servers and their discovered tools."""
      from orchid.config import configure_for_project
      from orchid.mcp.manager import MCPManager
      proj_cfg = configure_for_project(project)
      mgr = MCPManager(proj_cfg)
      mgr.start_all()
      for adapter in mgr.get_adapters():
          names = adapter.get_tool_names()
          typer.echo(f"{adapter._server_name}: {len(names)} tools")
          for n in names:
              typer.echo(f"  {n}")
      mgr.stop_all()

  @mcp_app.command("test")
  def _cmd_mcp_test(
      server_name: str = typer.Argument(..., help="Server name from .orchid.yaml"),
      project: str = typer.Option(".", "--project", "-p"),
  ):
      """Connect to one MCP server, list its tools, and disconnect."""
      from orchid.config import configure_for_project
      from orchid.mcp.manager import MCPManager
      from orchid.mcp.client import MCPClientError
      proj_cfg = configure_for_project(project)
      mgr = MCPManager(proj_cfg)
      # Only start the named server: filter _server_configs
      mgr._server_configs = [c for c in mgr._server_configs if c["name"] == server_name]
      if not mgr._server_configs:
          typer.echo(f"No server named '{server_name}' in .orchid.yaml", err=True)
          raise typer.Exit(1)
      mgr.start_all()
      if not mgr.get_adapters():
          typer.echo(f"Failed to connect to '{server_name}'", err=True)
          raise typer.Exit(1)
      for adapter in mgr.get_adapters():
          typer.echo(adapter.get_tools_for_prompt())
      mgr.stop_all()
  ```
  `type:code_generate` `p2` `needs:T110`

### Review

- [ ] **T116** Review `orchid/mcp/stdio_client.py` for exactly these 4 issues. For each, report PASS or FAIL with the line number:
  1. `_recv()` — does it use `select.select` with `self._timeout` before reading stdout? FAIL if it calls `readline()` without any timeout.
  2. `disconnect()` — does it call `self._process.wait(timeout=5)` and then `self._process.kill()` if `wait` raises `subprocess.TimeoutExpired`?
  3. `connect()` — does it merge env vars with `{**os.environ, **(self._env or {})}` so the subprocess inherits PATH?
  4. `_send()` — does it call `self._process.stdin.flush()` after writing the JSON line?
  `type:review` `p1` `needs:T107`

- [ ] **T117** Review `orchid/mcp/http_client.py` for exactly these 4 issues. For each, report PASS or FAIL with the line number:
  1. `_post()` — is `httpx.Client` created with `follow_redirects=False`?
  2. `_post()` — does it catch `httpx.TimeoutException` and re-raise as `MCPClientError("timeout")`?
  3. `_post()` — on non-200 status, does the `MCPClientError` message include both the status code and the first 200 chars of the response body?
  4. `connect()` — does it wrap `httpx.ConnectError` and re-raise as `MCPClientError`?
  `type:review` `p1` `needs:T108`

- [ ] **T118** Review `orchid/mcp/adapter.py` for exactly these 3 issues. For each, report PASS or FAIL with the line number:
  1. `get_tools_for_prompt()` — is description sliced to `[:_MAX_DESC]` (200 chars) before inclusion in the prompt string?
  2. `execute()` — when `MCPResult.is_error` is True, does the returned string start with `"[ERROR: "` so agents can detect failures?
  3. `load_tools()` — does it call `filter_tools()` before storing to `self._tools`, ensuring `self._tools` only ever contains allowed tools?
  `type:review` `p1` `needs:T109`

### Testing

- [ ] **T119** Create `tests/test_mcp_types.py`. Write exactly these 3 test functions, no fixtures needed:
  ```python
  def test_mcp_tool_default_params_empty():
      from orchid.mcp.types import MCPTool
      t = MCPTool(name="x", description="y")
      assert t.params == []

  def test_mcp_result_not_error_by_default():
      from orchid.mcp.types import MCPResult
      r = MCPResult(content="ok")
      assert r.is_error is False

  def test_mcp_tool_param_required_default():
      from orchid.mcp.types import MCPToolParam
      p = MCPToolParam(name="p", type="string", description="d")
      assert p.required is True
  ```
  `type:test_write` `p1` `needs:T105`

- [ ] **T120** Create `tests/test_mcp_stdio_client.py`. Write exactly these 4 test functions using `unittest.mock.patch`:
  ```python
  # test_list_tools_parses_jsonrpc_response:
  #   Mock subprocess.Popen so stdout.readline() returns the bytes of this JSON line:
  #   {"jsonrpc":"2.0","id":1,"result":{}}   (initialize response)
  #   then {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"echo","description":"echoes"}]}}
  #   Call client.connect() then client.list_tools().
  #   Assert result == [MCPTool(name="echo", description="echoes")]

  # test_call_tool_sends_correct_jsonrpc:
  #   Capture bytes written to mock stdin.
  #   Call client.call_tool("echo", {"msg": "hi"}).
  #   Parse the written JSON. Assert method=="tools/call", params=={"name":"echo","arguments":{"msg":"hi"}}.

  # test_server_exit_raises_mcp_client_error:
  #   Mock stdout.readline() returns b"" (EOF).
  #   Call client._recv(). Assert raises MCPClientError.

  # test_disconnect_terminates_process:
  #   After connect(), call disconnect().
  #   Assert mock_process.terminate() was called.
  ```
  `type:test_write` `p1` `needs:T107`

- [ ] **T121** Create `tests/test_mcp_http_client.py`. Write exactly these 3 test functions using `respx` to mock httpx:
  ```python
  # test_list_tools_parses_response:
  #   Mock POST /tools/list -> 200 {"tools":[{"name":"search","description":"web search"}]}
  #   Call client.connect() then client.list_tools().
  #   Assert result == [MCPTool(name="search", description="web search")]

  # test_call_tool_sends_correct_body:
  #   Mock POST /tools/call -> 200 {"content":"result","isError":false}
  #   Capture request body. Call client.call_tool("search", {"q": "python"}).
  #   Assert request body == {"name": "search", "arguments": {"q": "python"}}.

  # test_non_200_raises_mcp_client_error:
  #   Mock POST /tools/list -> 500 "Internal error"
  #   Call client.list_tools(). Assert raises MCPClientError with "HTTP 500" in str(exc).
  ```
  `type:test_write` `p1` `needs:T108`

- [ ] **T122** Create `tests/test_mcp_adapter.py`. Write exactly these 4 test functions. Use `unittest.mock.MagicMock` for the client:
  ```python
  # test_tool_names_are_namespaced:
  #   mock_client.list_tools.return_value = [MCPTool("issues", "list issues")]
  #   adapter = MCPToolAdapter("gh", mock_client)
  #   adapter.load_tools()
  #   assert adapter.get_tool_names() == ["mcp__gh__issues"]

  # test_allowed_tools_filter:
  #   mock_client.list_tools.return_value = [MCPTool("issues","x"), MCPTool("pulls","y")]
  #   adapter = MCPToolAdapter("gh", mock_client, allowed_tools=["issues"])
  #   adapter.load_tools()
  #   assert adapter.get_tool_names() == ["mcp__gh__issues"]

  # test_execute_returns_content:
  #   mock_client.call_tool.return_value = MCPResult(content="42 results")
  #   result = adapter.execute("mcp__gh__issues", {})
  #   assert result == "42 results"

  # test_execute_error_is_prefixed:
  #   mock_client.call_tool.return_value = MCPResult(content="not found", is_error=True)
  #   result = adapter.execute("mcp__gh__issues", {})
  #   assert result.startswith("[ERROR: ")
  ```
  `type:test_write` `p1` `needs:T109`

- [ ] **T123** Create `tests/test_mcp_manager.py`. Write exactly these 3 test functions using `unittest.mock.patch`:
  ```python
  # test_disabled_server_is_skipped:
  #   project_config = {"mcp_servers": [{"name":"gh","kind":"stdio","command":"npx",
  #     "args":[],"enabled":False,"allowed_tools":None}]}
  #   mgr = MCPManager(project_config)
  #   mgr.start_all()
  #   assert mgr.get_adapters() == []

  # test_start_all_populates_adapters:
  #   Patch StdioMCPClient.connect and StdioMCPClient.list_tools (returns [MCPTool("t","d")]).
  #   project_config = {"mcp_servers": [{"name":"test","kind":"stdio","command":"echo",
  #     "args":[],"enabled":True,"allowed_tools":None}]}
  #   mgr = MCPManager(project_config)
  #   mgr.start_all()
  #   assert len(mgr.get_adapters()) == 1

  # test_execute_mcp_action_routes_correctly:
  #   Two adapters: "gh" (tools: ["issues"]) and "jira" (tools: ["ticket"]).
  #   Call mgr.execute_mcp_action("mcp__gh__issues", {}).
  #   Assert gh adapter._client.call_tool was called; jira adapter._client.call_tool was NOT called.
  ```
  `type:test_write` `p1` `needs:T110`

- [ ] **T124** Create `tests/test_mcp_integration.py`. Write exactly 1 test function. Start a real Python subprocess as a minimal MCP server using `python3 -c "<inline script>"`. The inline script must: listen on stdin, respond to `initialize` with `{"jsonrpc":"2.0","id":0,"result":{}}`, respond to `tools/list` with one tool named `echo`, respond to `tools/call` with `{"content": arguments["msg"], "isError": false}`. Test: `StdioMCPClient.connect()`, `list_tools()` returns `[MCPTool("echo",…)]`, `call_tool("echo", {"msg":"hello"})` returns `MCPResult(content="hello")`. Use `pytest.mark.skipif(sys.platform=="win32", reason="POSIX only")`. `type:test_write` `p1` `needs:T107`

---

## Feature 3: Stream-JSON Output

Close gap: *Claude Code has `--output-format stream-json`. Orchid has no machine-readable output.*  
Design decisions (resolved here):
- Events are plain `@dataclass` classes with a `to_json() -> str` method. No base class inheritance needed.
- Three emitter classes live in separate files: `NullEmitter` (default), `NdJsonEmitter` (stdout/file), `WebSocketEmitter` (reuses existing WS `stream_callback` pattern in `Orchestrator`).
- CLI flag `--output-format` added to `orchid/interfaces/cli.py` `main()` typer function.
- Wired into `Orchestrator._execute_task` via existing `self.stream_callback` mechanism — no new fields on Orchestrator needed.
- Web endpoint added to `orchid/web/server.py`.

### Code Generation

- [ ] **T125** Create `orchid/output/events.py`. Also create empty `orchid/output/__init__.py` with content `# Stream output events`. Define exactly these dataclasses. All fields must have defaults so instances can be created with only the unique fields:
  ```python
  import json, time
  from dataclasses import dataclass, field

  def _ts() -> float:
      return time.time()

  @dataclass
  class SessionStartEvent:
      type: str = "session_start"
      session_id: str = ""
      project: str = ""
      mode: str = ""
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class TaskStartEvent:
      type: str = "task_start"
      session_id: str = ""
      task_id: str = ""
      task_title: str = ""
      task_type: str = ""
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class AgentThoughtEvent:
      type: str = "agent_thought"
      session_id: str = ""
      task_id: str = ""
      thought: str = ""
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class ToolUseEvent:
      type: str = "tool_use"
      session_id: str = ""
      task_id: str = ""
      tool: str = ""
      input_summary: str = ""   # first 200 chars of JSON-encoded input only — no secrets
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class ToolResultEvent:
      type: str = "tool_result"
      session_id: str = ""
      task_id: str = ""
      tool: str = ""
      output_summary: str = ""  # first 200 chars of output
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class TaskCompleteEvent:
      type: str = "task_complete"
      session_id: str = ""
      task_id: str = ""
      duration_s: float = 0.0
      iterations: int = 0
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class TaskFailEvent:
      type: str = "task_fail"
      session_id: str = ""
      task_id: str = ""
      error: str = ""
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)

  @dataclass
  class SessionEndEvent:
      type: str = "session_end"
      session_id: str = ""
      task_count: int = 0
      duration_s: float = 0.0
      ts: float = field(default_factory=_ts)
      def to_json(self) -> str: return json.dumps(self.__dict__)
  ```
  `type:code_generate` `p1`

- [ ] **T126** Create `orchid/output/emitter.py`. Define a protocol class and `NullEmitter`. No imports from `orchid.output.events` needed — accept any object with `to_json()`:
  ```python
  from typing import Protocol, runtime_checkable

  @runtime_checkable
  class StreamEmitter(Protocol):
      def emit(self, event) -> None: ...
      # event: any object with a .to_json() -> str method

  class NullEmitter:
      """Default emitter — discards all events silently."""
      def emit(self, event) -> None:
          pass
  ```
  `type:code_generate` `p1` `needs:T125`

- [ ] **T127** Create `orchid/output/ndjson_emitter.py`. Implement exactly:
  ```python
  import sys
  from typing import IO

  class NdJsonEmitter:
      """Writes one JSON line per event to a file-like object (default: sys.stdout)."""

      def __init__(self, stream: IO[str] | None = None):
          self._stream = stream or sys.stdout

      def emit(self, event) -> None:
          # Calls event.to_json(), writes result + "\n" to self._stream, flushes.
          # If event.to_json() raises: silently pass (never crash caller).
          try:
              self._stream.write(event.to_json() + "\n")
              self._stream.flush()
          except Exception:
              pass
  ```
  `type:code_generate` `p1` `needs:T126`

- [ ] **T128** Create `orchid/output/ws_emitter.py`. Implement exactly:
  ```python
  from typing import Callable, Any

  class WebSocketEmitter:
      """Adapts a stream_callback(dict) to the StreamEmitter protocol.

      Re-uses the existing Orchestrator.stream_callback infrastructure.
      Converts event.to_json() -> parses back to dict -> calls callback.
      """

      def __init__(self, callback: Callable[[dict[str, Any]], None]):
          self._callback = callback

      def emit(self, event) -> None:
          import json
          try:
              self._callback(json.loads(event.to_json()))
          except Exception:
              pass
  ```
  `type:code_generate` `p1` `needs:T126`

- [ ] **T129** Extend `orchid/orchestrator.py` — emit task events via `stream_callback`. Read the file first. The existing `self.stream_callback` at line 136 already sends dicts. Extend `_execute_task` to also emit typed stream events using the emitter if set. Make exactly these changes:
  1. Add `self.emitter = None` on a new line after `self.stream_callback = None` (line 136 area). Type hint: `# StreamEmitter | None`.
  2. At the start of `_execute_task`, after `logger.info("Executing task...")`, add:
     ```python
     _t0 = time.time()
     if self.emitter:
         from orchid.output.events import TaskStartEvent
         self.emitter.emit(TaskStartEvent(
             session_id=str(self.session.project_dir),
             task_id=task.id, task_title=task.title, task_type=task.type,
         ))
     ```
  3. Find where task status is set to `done` (search for `"status": "done"`). After that line, add:
     ```python
     if self.emitter:
         from orchid.output.events import TaskCompleteEvent
         self.emitter.emit(TaskCompleteEvent(
             session_id=str(self.session.project_dir),
             task_id=task.id, duration_s=time.time() - _t0,
         ))
     ```
  4. In the exception handler near the bottom of `_execute_task`, add:
     ```python
     if self.emitter:
         from orchid.output.events import TaskFailEvent
         self.emitter.emit(TaskFailEvent(
             session_id=str(self.session.project_dir),
             task_id=task.id, error=str(e)[:200],
         ))
     ```
  Add `import time` at top of file if not already present.
  `type:code_generate` `p1` `needs:T128` `needs:T113`

- [ ] **T130** Extend `orchid/runner.py` — emit session-level events. Read the file first. In `BackgroundRunner._run()`, make exactly these changes:
  1. After `mcp_manager.start_all()` (added in T114), add:
     ```python
     from orchid.output.events import SessionStartEvent, SessionEndEvent
     _session_start = time.time()
     if orch.emitter:
         orch.emitter.emit(SessionStartEvent(
             session_id=project_path, project=project_path, mode="auto"
         ))
     ```
  2. In the `finally:` block, before `mcp_manager.stop_all()`, add:
     ```python
     if orch.emitter:
         orch.emitter.emit(SessionEndEvent(
             session_id=project_path,
             task_count=state.tasks_done,
             duration_s=time.time() - _session_start,
         ))
     ```
  Add `import time` at top of file if not already present.
  `type:code_generate` `p1` `needs:T129`

- [ ] **T131** Extend `orchid/interfaces/cli.py` — add `--output-format` option to the `main()` typer function and wire emitter into `_cmd_auto`. Read the file first. Make exactly these changes:
  1. Find `def main(` (line 81). Add parameter:
     ```python
     output_format: str = typer.Option("text", "--output-format",
         help="Output format: text (default), stream-json (NDJSON to stdout)."),
     ```
  2. Pass `output_format` down to `_cmd_auto()` by adding it as a parameter there too.
  3. In `_cmd_auto()`, after `orch = Orchestrator(session, ...)` is constructed, add:
     ```python
     if output_format == "stream-json":
         import logging, sys
         from orchid.output.ndjson_emitter import NdJsonEmitter
         orch.emitter = NdJsonEmitter(sys.stdout)
         # Redirect all log output to stderr so stdout stays clean JSON
         logging.getLogger().handlers = [h for h in logging.getLogger().handlers
                                          if not isinstance(h, logging.StreamHandler)
                                          or h.stream is sys.stderr]
         stderr_handler = logging.StreamHandler(sys.stderr)
         stderr_handler.setLevel(logging.WARNING)
         logging.getLogger().addHandler(stderr_handler)
     ```
  `type:code_generate` `p1` `needs:T129`

- [ ] **T132** Extend `orchid/web/server.py` — add NDJSON streaming endpoint. Read the file first. Find the FastAPI app instance and existing `/api/projects/{project_id}/run` route (or equivalent run endpoint). Add a new route:
  ```python
  @app.get("/api/projects/{project_id}/run/stream")
  async def stream_run(project_id: str, request: Request):
      """Stream task events as NDJSON while a run is in progress.

      Polls the runner's stream_callback queue and yields events as newline-delimited JSON.
      Returns 404 if project not found. Returns 200 with media_type application/x-ndjson.
      """
      import asyncio, queue
      from starlette.responses import StreamingResponse
      from orchid.output.ws_emitter import WebSocketEmitter

      # Resolve project path from project_id (use existing project registry logic).
      # If not found: return JSONResponse({"error": "not found"}, status_code=404)
      # Create a thread-safe queue.Queue() for events.
      # Create WebSocketEmitter(lambda d: event_queue.put_nowait(d)).
      # Attach emitter to the project's Orchestrator via runner.
      # Stream: async generator that polls queue every 0.1s, yields json.dumps(item)+"\n".
      # Stop when run completes (check runner.get_status(project_path)["running"] == False and queue empty).
      ...
  ```
  Write the full implementation. Use `queue.Queue` for thread-safe event passing from the sync orchestrator thread to the async FastAPI response.
  `type:code_generate` `p2` `needs:T128,T129`

### Review

- [ ] **T133** Review `orchid/output/events.py` for exactly these 3 issues. Report PASS or FAIL with line number:
  1. Does every event dataclass include both `session_id` and `ts` fields?
  2. Does `ToolUseEvent.input_summary` and `ToolResultEvent.output_summary` enforce a 200-char cap in `to_json()` or at construction time? (Check that full tool input/output content is never serialised.)
  3. Does `to_json()` handle non-serialisable field values (e.g. `Path` objects) without raising? (Check that all fields are JSON-native types: str, float, int.)
  `type:review` `p1` `needs:T125`

- [ ] **T134** Review `orchid/output/ndjson_emitter.py` for exactly these 2 issues. Report PASS or FAIL with line number:
  1. Does `emit()` call `self._stream.flush()` after every write so consumers receive events in real time?
  2. Does `emit()` wrap the entire body in `try/except Exception: pass` so a serialisation error never propagates to the caller?
  `type:review` `p1` `needs:T127`

### Testing

- [ ] **T135** Create `tests/test_output_events.py`. Write exactly these 3 test functions:
  ```python
  def test_session_start_event_to_json():
      import json
      from orchid.output.events import SessionStartEvent
      e = SessionStartEvent(session_id="s1", project="/tmp/p", mode="auto")
      d = json.loads(e.to_json())
      assert d["type"] == "session_start"
      assert d["session_id"] == "s1"
      assert "ts" in d

  def test_tool_use_event_has_no_raw_input():
      # Verifies that ToolUseEvent does NOT include a full "input" field — only input_summary.
      from orchid.output.events import ToolUseEvent
      import json
      e = ToolUseEvent(session_id="s", task_id="T001", tool="bash",
                       input_summary="ls /tmp")
      d = json.loads(e.to_json())
      assert "input_summary" in d
      assert "input" not in d  # raw input must never appear

  def test_all_event_types_have_ts():
      from orchid.output import events
      import dataclasses, json
      event_classes = [
          events.SessionStartEvent, events.TaskStartEvent, events.AgentThoughtEvent,
          events.ToolUseEvent, events.ToolResultEvent, events.TaskCompleteEvent,
          events.TaskFailEvent, events.SessionEndEvent,
      ]
      for cls in event_classes:
          d = json.loads(cls().to_json())
          assert "ts" in d, f"{cls.__name__} missing ts field"
  ```
  `type:test_write` `p1` `needs:T125`

- [ ] **T136** Create `tests/test_ndjson_emitter.py`. Write exactly these 3 test functions:
  ```python
  def test_emit_writes_valid_json_line():
      import io, json
      from orchid.output.ndjson_emitter import NdJsonEmitter
      from orchid.output.events import TaskStartEvent
      buf = io.StringIO()
      emitter = NdJsonEmitter(stream=buf)
      emitter.emit(TaskStartEvent(session_id="s", task_id="T001", task_title="x"))
      buf.seek(0)
      line = buf.read().strip()
      d = json.loads(line)
      assert d["type"] == "task_start"

  def test_emit_flushes_after_write():
      import io
      from orchid.output.ndjson_emitter import NdJsonEmitter
      from orchid.output.events import SessionEndEvent
      buf = io.StringIO()
      emitter = NdJsonEmitter(stream=buf)
      emitter.emit(SessionEndEvent())
      # If flush was called, getvalue() will contain the written content immediately
      assert buf.getvalue() != ""

  def test_emit_does_not_raise_on_bad_event():
      from orchid.output.ndjson_emitter import NdJsonEmitter
      import io
      class BadEvent:
          def to_json(self): raise ValueError("serialisation failure")
      emitter = NdJsonEmitter(stream=io.StringIO())
      emitter.emit(BadEvent())  # must not raise
  ```
  `type:test_write` `p1` `needs:T127`

- [ ] **T137** Create `tests/test_stream_json_cli.py`. Write exactly 1 test function that invokes the CLI with `--output-format stream-json` using `subprocess.run`:
  ```python
  def test_stream_json_output_is_parseable(tmp_path):
      import subprocess, sys, json
      # Set up a minimal project with one tasks.md containing one simple task.
      # Run: orchid --project <tmp_path> --mode auto --output-format stream-json
      # Capture stdout. Assert:
      # 1. Every line in stdout parses as valid JSON.
      # 2. At least one line has type == "session_start".
      # 3. At least one line has type == "session_end".
      # 4. stderr does NOT contain any JSON (it should contain only log lines).
      ...
  ```
  Write the full test body including project setup (create `tasks.md` with `- [ ] **T001** Write hello world to hello.txt \`type:code_generate\` \`p1\``).
  `type:test_write` `p1` `needs:T129,T130,T131`

---

## Feature 4: Session Checkpointing

Close gap: *Claude Code has `/rewind`. Orchid task results are irreversible.*  
Design decisions (resolved here):
- Checkpoints stored in `<project>/.orchid/checkpoints/<task_id>.json` (no session_id needed — task_id is unique within a project run).
- `FileSnapshot` stores full file content (not just sha256) so restore can write it back.
- `tasks.md` rewrite uses `orchid.memory.state.save_tasks()` which already exists at `orchid/memory/state.py`.
- Restore is atomic: write to `<path>.tmp` then `os.replace(tmp, path)`.
- CLI flags added to `orchid/interfaces/cli.py` `main()` typer function.

### Code Generation

- [ ] **T138** Create `orchid/checkpoint/schema.py`. Also create empty `orchid/checkpoint/__init__.py` with content `# Session checkpoint`. Define exactly these dataclasses:
  ```python
  from dataclasses import dataclass, field
  import time

  @dataclass
  class FileSnapshot:
      path: str           # relative to project_dir
      content: str        # full file content at checkpoint time

  @dataclass
  class TaskSnapshot:
      id: str
      status: str         # "todo" | "in_progress" | "done" | "blocked" | "skip"
      result_snippet: str = ""  # first 500 chars of task result, empty if not yet done

  @dataclass
  class Checkpoint:
      task_id: str                                    # task that is ABOUT TO run
      project_dir: str                                # absolute path to project
      created_at: float = field(default_factory=time.time)
      tasks_state: list[TaskSnapshot] = field(default_factory=list)
      files_snapshot: list[FileSnapshot] = field(default_factory=list)
  ```
  `type:code_generate` `p1`

- [ ] **T139** Create `orchid/checkpoint/store.py`. Implement exactly this class:
  ```python
  import json, os
  from pathlib import Path
  from orchid.checkpoint.schema import Checkpoint, TaskSnapshot, FileSnapshot

  class CheckpointNotFound(Exception):
      pass

  class CheckpointStore:
      def __init__(self, project_dir: str | Path):
          self._dir = Path(project_dir) / ".orchid" / "checkpoints"
          self._dir.mkdir(parents=True, exist_ok=True)

      def save(self, checkpoint: Checkpoint) -> Path:
          # Serialise checkpoint to JSON. Write to self._dir / f"{checkpoint.task_id}.json".
          # Use atomic write: write to .tmp then os.replace(.tmp, target).
          # Returns the path written.
          ...

      def load(self, task_id: str) -> Checkpoint:
          # Read self._dir / f"{task_id}.json". Raise CheckpointNotFound if missing.
          # Deserialise: reconstruct Checkpoint with nested TaskSnapshot and FileSnapshot lists.
          ...

      def list_checkpoints(self) -> list[str]:
          # Return sorted list of task_id strings (stem of .json files), oldest first (by mtime).
          ...

      def prune(self, keep_last: int = 10) -> int:
          # Delete oldest checkpoints keeping only the most recent `keep_last`.
          # Sort by mtime ascending. Delete files beyond keep_last. Return count deleted.
          ...
  ```
  `type:code_generate` `p1` `needs:T138`

- [ ] **T140** Create `orchid/checkpoint/restore.py`. Implement exactly these two functions:
  ```python
  import os
  from pathlib import Path
  from orchid.checkpoint.schema import Checkpoint
  from orchid.checkpoint.store import CheckpointStore

  def restore(task_id: str, project_dir: str | Path, dry_run: bool = False) -> Checkpoint:
      """Load checkpoint for task_id and restore project state.

      Steps:
      1. Load checkpoint via CheckpointStore(project_dir).load(task_id).
      2. For each FileSnapshot in checkpoint.files_snapshot:
         - If dry_run: print f"  RESTORE {snap.path} ({len(snap.content)} chars)"
         - Else: write snap.content to Path(project_dir) / snap.path atomically
                 (write to .tmp, os.replace). Create parent dirs if needed.
      3. Restore tasks.md task statuses:
         - Read current tasks.md using orchid.memory.state.load_tasks(project_dir).
         - For each task in loaded tasks: if task.id is in checkpoint.tasks_state,
           set task.status = TaskStatus[snapshot.status.upper()].
         - If dry_run: print the status changes. Else: call orchid.memory.state.save_tasks(tasks, project_dir).
      4. Return the loaded Checkpoint.
      """
      ...

  def find_latest_checkpoint(project_dir: str | Path) -> str | None:
      """Return task_id of the most recently created checkpoint, or None if none exist."""
      store = CheckpointStore(project_dir)
      checkpoints = store.list_checkpoints()
      return checkpoints[-1] if checkpoints else None
  ```
  `type:code_generate` `p1` `needs:T139`

- [ ] **T141** Extend `orchid/orchestrator.py` — capture checkpoint before each task. Read the file first. In `_execute_task` (line 192), find the line `self.session.update_task_status(task.id, TaskStatus.IN_PROGRESS)`. Add the following block **before** that line:
  ```python
  # Capture pre-task checkpoint
  try:
      from orchid.checkpoint.store import CheckpointStore
      from orchid.checkpoint.schema import Checkpoint, TaskSnapshot, FileSnapshot
      _ckpt_store = CheckpointStore(self.session.project_dir)
      _tasks_snap = [
          TaskSnapshot(id=t.id, status=t.status.value,
                       result_snippet=(t.result or "")[:500])
          for t in self.session.tasks
      ]
      _files_snap = []
      for _fpath in self._tracked_files:
          try:
              _full = (Path(self.session.project_dir) / _fpath).read_text(errors="replace")
              _files_snap.append(FileSnapshot(path=_fpath, content=_full))
          except OSError:
              pass
      _ckpt = Checkpoint(task_id=task.id,
                         project_dir=str(self.session.project_dir),
                         tasks_state=_tasks_snap,
                         files_snapshot=_files_snap)
      _ckpt_store.save(_ckpt)
  except Exception:
      logger.debug("Checkpoint capture failed (non-fatal)", exc_info=True)
  ```
  Also add `self._tracked_files: set[str] = set()` in `Orchestrator.__init__` after the existing instance vars.
  Find where `write_file` results are processed in `_execute_task` (search for `"files_written"`). After that, add:
  ```python
  self._tracked_files.update(result.get("files_written", []))
  ```
  Add `from pathlib import Path` at top of file if not already imported.
  `type:code_generate` `p1` `needs:T139` `needs:T113`

- [ ] **T142** Extend `orchid/runner.py` — prune checkpoints at session end. Read the file first. In `BackgroundRunner._run()` `finally:` block, before `mcp_manager.stop_all()` (added in T114), add:
  ```python
  try:
      from orchid.checkpoint.store import CheckpointStore
      keep = proj_cfg.get("checkpointing", {}).get("keep_last", 10)
      CheckpointStore(project_path).prune(keep_last=keep)
  except Exception:
      logger.debug("Checkpoint prune failed (non-fatal)", exc_info=True)
  ```
  `type:code_generate` `p2` `needs:T139,T114`

- [ ] **T143** Extend `orchid/interfaces/cli.py` — add `--rewind`, `--resume`, and `--list-checkpoints` options to `main()`. Read the file first. Add these three option parameters to `def main(`:
  ```python
  rewind: str | None = typer.Option(None, "--rewind",
      help="Restore project state to before task <TASK_ID> ran."),
  resume: bool = typer.Option(False, "--resume",
      help="Re-run from the latest checkpoint after a crash."),
  list_checkpoints: bool = typer.Option(False, "--list-checkpoints",
      help="Print checkpoint table and exit."),
  ```
  Add handling at the **top** of `main()` body, before any other command dispatch:
  ```python
  if list_checkpoints:
      _cmd_list_checkpoints(project=project)
      raise typer.Exit()
  if rewind:
      _cmd_rewind(task_id=rewind, project=project)
      raise typer.Exit()
  if resume:
      _cmd_resume(project=project)
      # fall through to normal auto run
  ```
  Add exactly these three helper functions near the bottom of the file (before `init` and `new_project`):
  ```python
  def _cmd_list_checkpoints(project: str) -> None:
      from orchid.checkpoint.store import CheckpointStore
      import datetime
      store = CheckpointStore(_resolve_project(project))
      ids = store.list_checkpoints()
      if not ids:
          typer.echo("No checkpoints found.")
          return
      typer.echo(f"{'TASK_ID':<12} {'CREATED':<22}")
      typer.echo("-" * 36)
      for tid in ids:
          ckpt = store.load(tid)
          dt = datetime.datetime.fromtimestamp(ckpt.created_at).strftime("%Y-%m-%d %H:%M:%S")
          typer.echo(f"{tid:<12} {dt:<22}")

  def _cmd_rewind(task_id: str, project: str, yes: bool = False) -> None:
      from orchid.checkpoint.restore import restore
      from orchid.checkpoint.store import CheckpointNotFound
      proj_path = _resolve_project(project)
      try:
          ckpt = restore(task_id, proj_path, dry_run=True)
      except CheckpointNotFound:
          typer.echo(f"No checkpoint found for task {task_id}", err=True)
          raise typer.Exit(1)
      typer.echo(f"Will restore {len(ckpt.files_snapshot)} file(s) and reset task statuses.")
      if not yes and not typer.confirm("Proceed?", default=False):
          raise typer.Exit(0)
      restore(task_id, proj_path, dry_run=False)
      typer.echo(f"Restored to pre-{task_id} state.")

  def _cmd_resume(project: str) -> None:
      from orchid.checkpoint.restore import find_latest_checkpoint
      proj_path = _resolve_project(project)
      latest = find_latest_checkpoint(proj_path)
      if not latest:
          typer.echo("No checkpoints found — starting fresh.", err=True)
          return
      typer.echo(f"Resuming from checkpoint before {latest}.")
      # restore without dry_run so task statuses are reset to re-run from this point
      from orchid.checkpoint.restore import restore
      restore(latest, proj_path, dry_run=False)
  ```
  `type:code_generate` `p1` `needs:T140`

### Review

- [ ] **T144** Review `orchid/checkpoint/store.py` for exactly these 4 issues. Report PASS or FAIL with line number:
  1. `save()` — does it use atomic write (`write to .tmp` then `os.replace(tmp, target)`) rather than writing directly to the target file?
  2. `load()` — does it raise `CheckpointNotFound` (not `KeyError` or `FileNotFoundError`) when the checkpoint file is missing?
  3. `prune()` — does it sort by file mtime (not alphabetical) so the oldest are deleted first?
  4. `save()` — are `tasks_state` and `files_snapshot` serialised as lists of dicts (not as dataclass objects), so `json.dumps` works without a custom encoder?
  `type:review` `p1` `needs:T139`

- [ ] **T145** Review `orchid/checkpoint/restore.py` for exactly these 3 issues. Report PASS or FAIL with line number:
  1. `restore()` — does it create parent directories (e.g. `Path(project_dir / snap.path).parent.mkdir(parents=True, exist_ok=True)`) before writing each restored file?
  2. `restore()` — does it use atomic file write (`write to .tmp` then `os.replace`) for each restored file?
  3. `restore()` — does it call `orchid.memory.state.save_tasks()` (not write `tasks.md` manually) to update task statuses?
  `type:review` `p1` `needs:T140`

### Testing

- [ ] **T146** Create `tests/test_checkpoint_schema.py`. Write exactly these 3 test functions:
  ```python
  def test_checkpoint_default_created_at_is_recent():
      import time
      from orchid.checkpoint.schema import Checkpoint
      before = time.time()
      c = Checkpoint(task_id="T001", project_dir="/tmp/p")
      after = time.time()
      assert before <= c.created_at <= after

  def test_file_snapshot_stores_content():
      from orchid.checkpoint.schema import FileSnapshot
      snap = FileSnapshot(path="src/foo.py", content="print('hi')")
      assert snap.content == "print('hi')"

  def test_task_snapshot_fields():
      from orchid.checkpoint.schema import TaskSnapshot
      snap = TaskSnapshot(id="T002", status="done", result_snippet="wrote 3 files")
      assert snap.id == "T002"
      assert snap.status == "done"
  ```
  `type:test_write` `p1` `needs:T138`

- [ ] **T147** Create `tests/test_checkpoint_store.py`. Write exactly these 4 test functions using `tmp_path` pytest fixture:
  ```python
  # test_save_and_load_roundtrip(tmp_path):
  #   Create Checkpoint(task_id="T001", project_dir=str(tmp_path),
  #     tasks_state=[TaskSnapshot("T001","todo")],
  #     files_snapshot=[FileSnapshot("foo.py","x=1")])
  #   store = CheckpointStore(tmp_path); store.save(c)
  #   loaded = store.load("T001")
  #   assert loaded.task_id == "T001"
  #   assert loaded.tasks_state[0].status == "todo"
  #   assert loaded.files_snapshot[0].content == "x=1"

  # test_load_missing_raises_checkpoint_not_found(tmp_path):
  #   store = CheckpointStore(tmp_path)
  #   with pytest.raises(CheckpointNotFound): store.load("MISSING")

  # test_list_checkpoints_sorted_by_mtime(tmp_path):
  #   Save T001, sleep 0.01s, save T002.
  #   assert store.list_checkpoints() == ["T001", "T002"]

  # test_prune_keeps_last_n(tmp_path):
  #   Save T001, T002, T003 with small sleeps between.
  #   store.prune(keep_last=2)
  #   assert store.list_checkpoints() == ["T002", "T003"]
  ```
  Write the full test body for each function.
  `type:test_write` `p1` `needs:T139`

- [ ] **T148** Create `tests/test_checkpoint_restore.py`. Write exactly these 2 test functions using `tmp_path`:
  ```python
  # test_restore_writes_files(tmp_path):
  #   Create Checkpoint with files_snapshot=[FileSnapshot("hello.py","print(1)")].
  #   Save via CheckpointStore. Call restore("T001", tmp_path).
  #   Assert (tmp_path / "hello.py").read_text() == "print(1)".

  # test_dry_run_does_not_write(tmp_path, capsys):
  #   Same setup. Call restore("T001", tmp_path, dry_run=True).
  #   Assert (tmp_path / "hello.py").exists() is False.
  #   Assert "RESTORE" in capsys.readouterr().out.
  ```
  Write the full test body for each function.
  `type:test_write` `p1` `needs:T140`

- [ ] **T149** Create `tests/test_checkpoint_integration.py`. Write exactly 1 test function using `tmp_path`:
  - Set up a minimal project with two tasks in `tasks.md`.
  - Create an `Orchestrator` with a patched agent that marks tasks done immediately.
  - Run `_execute_task` for task 1. Verify checkpoint file exists in `.orchid/checkpoints/T001.json`.
  - Run `_execute_task` for task 2. Verify `T002.json` also exists.
  - Call `restore("T002", tmp_path)`. Verify task 2 status is reset to `"todo"` in the reloaded `tasks.md`.
  `type:test_write` `p1` `needs:T141,T140`

---

## Rollup

- [ ] **T150** Gap-closure sprint rollup `type:rollup` `p1` `rollup:T092,T093,T094,T095,T096,T097,T098,T099,T100,T101,T102,T103,T104,T105,T106,T107,T108,T109,T110,T111,T112,T113,T114,T115,T116,T117,T118,T119,T120,T121,T122,T123,T124,T125,T126,T127,T128,T129,T130,T131,T132,T133,T134,T135,T136,T137,T138,T139,T140,T141,T142,T143,T144,T145,T146,T147,T148,T149` `output:GAP-CLOSURE-REPORT.md`

---

## Summary

| Feature | Code tasks | Review tasks | Test tasks | Total |
|---------|-----------|-------------|-----------|-------|
| Hook system | T092–T099 (8) | T100–T101 (2) | T102–T104 (3) | 13 |
| MCP adapter | T105–T115 (11) | T116–T118 (3) | T119–T124 (6) | 20 |
| Stream-JSON output | T125–T132 (8) | T133–T134 (2) | T135–T137 (3) | 13 |
| Session checkpointing | T138–T143 (6) | T144–T145 (2) | T146–T149 (4) | 12 |
| **Total** | **33** | **9** | **16** | **58** |

**Execution order:** T092→T104 (hooks, independent). T105→T124 (MCP, T107+T108 can run in parallel after T106). T125→T137 (stream-json, T127+T128 parallel after T126; T129 needs T113 done first). T138→T149 (checkpointing, T141 needs T113 done first). Rollup T150 last.
