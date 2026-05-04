# Tasks


## TODO

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

## DONE

- [x] **T088** Fix Discussion panel focus: after AI responds in the Discussion tab the message input loses focus and clicking it doesn't restore it. User has to leave the panel and come back. Fix: after each AI response completes, programmatically re-focus the message input using inputRef.current?.focus(). Also when the AI presents numbered options in its response, clicking an option fills the input but does not focus it — add focus() call after filling the input value. `type:code_generate` `p1`
- [x] **T089** Add loading indicator to Discussion panel when PM agent is generating artifacts: after user types 'done' or agent says it will generate REQUIREMENTS.md/ARCHITECTURE.md, show a visible loading state — spinner, progress message like 'Generating REQUIREMENTS.md...', and disable the input with 'Working...' placeholder. The backend already sends status callbacks via WebSocket (advance_status events) — ensure the frontend is listening for these and displaying them. This was implemented in T053 but may have regressed. `type:code_generate` `p1`
- [x] **T090** Fix lifecycle phase display: orchid --phase and Web UI phase indicator should not list the current phase as an advancement target in 'Can advance to:'. In gates.py or lifecycle.py, filter out the current phase from the list of possible next phases. Also verify the phase indicator in the Web UI PhaseIndicator component does not show the current phase as clickable/next. `type:code_generate` `p1`
- [x] **T087** Add per-agent provider overrides to .orchid.yaml: extend provider registry to support named agent overrides (discussion, product_manager, project_manager, developer, reviewer, orchestrator) in the providers: section of .orchid.yaml. These override type defaults but are overridden by CLI --provider flags. Update orchid.defaults.yaml with commented examples. Update DiscussionAgent, ProductManagerAgent, ProjectManagerAgent to check their named provider key before falling back to type default. This allows e.g. providers: discussion: local to route all PM planning through local model without --offline flag. `type:code_generate` `p1`
- [x] **T086** Create docs/pm-guide.md: a comprehensive PM walkthrough guide covering the full idea-to-execution pipeline in Orchid V2. Include: 1) Overview of the PM workflow (Discussion → Requirements → Planning → Execution → Review), 2) Starting a new project via Web UI New Project Wizard with screenshot placeholder, 3) The Discussion phase — chatting with the AI to refine requirements with screenshot placeholder, 4) Reviewing generated REQUIREMENTS.md and ARCHITECTURE.md in the Planning tab with screenshot placeholder, 5) Approving the plan to move to execution with screenshot placeholder, 6) Monitoring execution via the PM Dashboard — Milestone Progress, Dependency Graph, Session Burndown, Task Timing with screenshot placeholders for each, 7) Understanding task statuses (TODO/IN_PROGRESS/DONE/BLOCKED/SKIP), 8) Using Telegram and Slack for mobile project monitoring — /orchid_projects, /orchid_switch, /orchid_approve commands, 9) Reading the rollup milestone summaries (MILESTONE-1.md etc), 10) Glossary of terms (task types, phases, agents). Use [SCREENSHOT: description] placeholders throughout. Write in plain English for a non-technical PM audience. `type:draft` `p1`
- [x] **T075** Fix Planning tab artifact panels: text content in Requirements, Architecture, Milestones and tasks.md tabs is not scrollable when it exceeds the viewport. Fixed: min-height:0 on flex chain (.artifact-panel/body/view/content), overflow:hidden on panel-body when Planning active, wrapper divs for READY/EXECUTING/COMPLETE phases get proper flex constraints. `type:code_generate` `p1`
- [x] **T074** Planning tab: show completed phase artifacts in read-only mode regardless of current phase. When project is in EXECUTING or COMPLETE phase, the Requirements, Architecture, Milestones and Tasks tabs should still display their content as read-only. Currently they show 'Project is executing' instead of the artifact content. Only hide/disable editing — never hide the content itself. `type:code_generate` `p1`
- [x] **T072** Fix Planning tab artifact panels: Requirements, Architecture, Milestones and tasks.md tabs should scroll independently even when not in edit mode. Added overflow:hidden/padding:0 to panel-body when Planning tab is active; existing flex chain now propagates height correctly so artifact-content can scroll. `type:code_generate` `p1`
- [x] **T073** Add SKIP task status to Orchid: orchid task skip --id T015 --project . marks task as skipped (shown as [~] in tasks.md). Skipped tasks are excluded from auto mode runs but count as satisfied for dependencies. Added Skip button to Web UI task board. `type:code_generate` `p1`
- [x] **T069** Add --run-task flag to CLI: orchid --project . --run-task T015 executes a single specific task. Added ▶ Run button to each task row in Web UI. Added POST /api/projects/{id}/tasks/{task_id}/run endpoint. `type:code_generate` `p1`
- [x] **T062** Fix Slack channel routing: added debug logging to _resolve_project and _get_project_for_channel showing channel_id received vs map contents. `type:code_generate` `p1`
- [x] **T059** Review the prompt caching implementation in orchid/providers/anthropic.py and confirm cache_control blocks are correctly applied `type:review` `p1`
- [x] **T058** Review the prompt caching implementation in orchid/providers/anthropic.py and confirm cache_control blocks are correctly applied `type:review` `p1`
- [x] **T057** Write a one-line comment to README.md describing Orchid V2 `type:draft` `p1`
- [x] **T056** Write a brief V2 feature summary to V2-SUMMARY.md covering: lifecycle phases, strategic agents, web UI planning tab, prompt caching `type:draft` `p1`
- [x] **T053** Fix DiscussionPanel loading state: when agent says it's ready to generate artifacts there is no visual indicator that work is happening. Add: 1) A loading spinner/progress bar when PM agent is running after 'done' is typed. 2) Status messages like 'Generating REQUIREMENTS.md...' and 'Generating ARCHITECTURE.md...' streamed via WebSocket. 3) Disable the input and show 'Working...' while agents are running. 4) Show a success banner when artifacts are ready. `type:code_generate` `p1`
- [x] **T050** Fix Planning tab scroll: content is not scrollable, text gets cut off. Check overflow CSS on PlanningTab, DiscussionPanel and ArtifactPanel components — add overflow-y: auto and appropriate max-height or height: 100% to allow scrolling. `type:code_generate` `p1`
- [x] **T051** Fix Planning tab scroll: content not scrollable in DiscussionPanel, ArtifactPanel and ApprovalPanel — add overflow-y:auto and proper height constraints so all content is reachable `type:code_generate` `p1`
- [x] **T052** Fix DiscussionPanel chat input focus: after sending a message the input loses focus and clicking it doesn't restore focus. After agent response is received, automatically re-focus the input element using inputRef.current.focus(). Also ensure clicking anywhere in the input area triggers focus. `type:code_generate` `p1`
- [x] **T046** Check all Python files in orchid/ for syntax errors using py_compile `type:review` `p1`
- [x] **T047** Check all imports in orchid/ are resolvable `type:review` `p1`
- [x] **T048** Verify test suite passes: run pytest tests/ and report results `type:review` `p1`
- [x] **T049** Orchid health rollup `type:rollup` `p1` `rollup:T046,T047,T048` `output:HEALTH-REPORT.md`
- [x] **T041** Add post-write verification to tools/filesystem.py: after writing a .js file automatically run 'node --input-type=module --eval "import('./file.js')"' to catch syntax errors and missing imports. After writing a .py file run 'python3 -m py_compile file.py'. Return verification result as part of the write_file observation so the agent can self-correct immediately. `type:code_generate` `p1`
- [x] **T042** Add new tool tools/consistency.py with check_imports(project_path) function: scan all .js files for import statements, verify each imported file exists at the expected path, return list of broken imports as {file, import, expected_path, exists}. Also scan .py files for imports and verify modules exist. Add 'Action: check_imports[path]' to ReAct parser. Reviewer agent should call this automatically at the end of each session. `type:code_generate` `p1`
- [x] **T040** Move Orchid machine-level config to XDG standard location ~/.config/orchid/.env — 1) load_dotenv() should search in order: cwd, ~/.config/orchid/.env, ~/LocalAI/orchid/.env (legacy fallback). 2) Create scripts/setup-config.sh that copies .env to ~/.config/orchid/.env and sets permissions 600. 3) Update orchid-serve.service EnvironmentFile to point to ~/.config/orchid/.env. 4) Update .env.example and README with new location. 5) After fixing run uv tool install . --force `type:code_generate` `p1`
- [x] **T038** Fix web server run trigger: when starting an agent run via POST /api/projects/{project_id}/run the project path passed to BackgroundRunner must be the absolute filesystem path from the project registry, not a path relative to the orchid working directory. Reproduce by triggering a run from the Web UI and checking where write_file calls resolve to. `type:code_generate` `p1`
- [x] **T036** Fix discovery.py: skip inotify watch setup for non-existent watch dirs instead of crashing. Also add exclude dirs to watchdog Observer to prevent watching .venv, node_modules, .git etc (inotify watch limit) `type:code_generate` `p1`
- [x] **T035** Add exponential backoff with jitter to AnthropicProvider.complete() for 429 rate limit errors — wait up to 60s between retries, max 3 retries, log warning on each retry `type:code_generate` `p1`
- [x] **T033** Fix offline mode: hot memory compression should use local provider when --offline flag is set, not call Claude API `type:code_generate` `p1`
- [x] **T032** Simple hello world function `type:code_generate` `p1`
- [x] **T031** Write a haiku about distributed systems `type:draft` `p1`
- [x] **T029** Test Web UI live task creation `type:draft` `p1`
- [x] **T025** Dependency test parent task `type:draft` `p1`
- [x] **T026** Dependency test child task `type:draft` `p1`
- [x] **T024** Write a complex regex parser for extracting structured data from session logs `type:code_generate` `p1`
- [x] **T023** Archive all completed tasks to tasks.md archive section now `type:code_generate` `p1`
- [x] **T022** Investigate and fix chunking producing oversized token payloads - chunks exceeding 1024 tokens despite chunk_size=400 word setting. Likely word-based chunking not accounting for tokenization overhead. Switch to token-based chunking with hard cap at 800 tokens. `type:code_generate` `p1`
- [x] **T017** Fix delegations counter not persisting in session status display `type:code_generate` `p1`
- [x] **T018** Fix D0011 truncating in CLAUDE.md compression - root cause is compression threshold too aggressive for growing decisions list `type:code_generate` `p1`
- [x] **T021** Run full test suite and fix any failing tests `type:code_generate` `p1`
- [x] **T014** Research best practices for Python async context managers, then implement one in orchid/session.py for safe session lifecycle management `type:code_generate` `p1`
- [x] **T011** Fix developer agent prompt to use delegate action for research-first tasks `type:code_generate` `p1`
- [x] **T012** Fix decisions.json Extra data parse error - persists after T008 `type:code_generate` `p1`
- [x] **T010** Research the best approach for implementing a retry mechanism in httpx, then implement a retry wrapper in orchid/tools/models.py using that approach `type:code_generate` `p1`
- [x] **T007** Filter ad results from DuckDuckGo backend (skip results with y.js URLs) `type:code_generate` `p1`
- [x] **T008** Fix decisions.json parse error - likely JSON Lines vs single JSON document format mismatch `type:code_generate` `p1`
- [x] **T002** Hook LLM summarizer into session compression `type:code_generate` `p1`
- [x] **T001** Review the session.py compression logic and suggest improvements `type:review` `p1`
- [x] **T103** Unit tests `type:draft` `p2` `needs:T094`
- [x] **T092** Design and implement `type:draft` `p2`
- [x] **T093** Define hook event constants in `type:draft` `p2` `needs:T092`
- [x] **T094** Implement hook loader `type:draft` `p2` `needs:T093`
- [x] **T097** Wire hooks into session and phase transitions: fire `type:draft` `p2` `needs:T094`
- [x] **T098** Add hook config schema to `type:draft` `p2` `needs:T094`
- [x] **T099** Add CLI: `type:draft` `p2` `needs:T098`
- [x] **T100** Review hook registry and loader implementation: verify blocking hooks cannot deadlock the orchestrator, shell hooks are sandboxed by the existing shell allowlist, http hooks respect timeout, and hook errors are logged but never crash the agent loop. Check `type:draft` `p2` `needs:T094,T095,T096,T097`
- [x] **T101** Review hook integration points in `type:draft` `p2` `needs:T095,T096`
- [x] **T102** Unit tests `type:draft` `p2` `needs:T092,T093`
- [x] **T104** Integration tests `type:draft` `p2` `needs:T095,T096,T097`
- [x] **T095** Wire hooks into agent ReAct loop `type:draft` `p2` `needs:T094`
- [x] **T096** Wire hooks into task lifecycle `type:draft` `p2` `needs:T094`
- [x] **T091** Update docs/pm-guide.md: add section on configuring fully-local operation via .orchid.yaml providers overrides. Show example config for all-local PM planning and development with Claude only for final review. Explain the resolution order: CLI flag > project config > task annotation > defaults. `type:draft` `p2`
- [x] **T085** Add task metrics capture to orchestrator: on every task completion (done/blocked/skipped) write a structured record to .orchid/task_metrics.jsonl containing: task_id, title, status, iters_used, iters_max, duration_s, action counts by type, model, session_id, and blocker details (reason, last_action, last_error) when blocked. Always-on, no flag needed. Add GET /api/projects/{id}/metrics endpoint returning parsed metrics. This feeds the PM dashboard and replaces need for full trace.log in Web UI. `type:code_generate` `p2`
- [x] **T084** Add PM Dashboard view to Web UI: read-only project management view accessible from main navigation. Components: 1) Milestone Progress — milestone name, task count, completed/blocked/pending breakdown, completion %. 2) Dependency Graph — visual DAG of tasks showing dependencies (needs:), critical path highlighted, blocked tasks in red, completed in green, pending in grey. Use a lightweight JS graph library (d3 or cytoscape.js). 3) Session Burn-down — tasks completed per session over time, bar chart per session showing completed/blocked/skipped counts. 4) Phase Timeline — for V2 lifecycle projects show time spent in each phase (DISCUSSING/REQUIREMENTS/PLANNING/EXECUTING) as a horizontal timeline. 5) Task Timing — table of completed tasks sorted by duration (from trace.log if available, session logs otherwise) showing fastest and slowest tasks. All views are read-only. Add PM tab to main navigation alongside Tasks/Planning/Stream etc. `type:code_generate` `p2`
- [x] **T079** Add venv/Docker awareness to agent bash tool: when running pytest or python in a project directory, check for .venv/bin/python, venv/bin/python, or docker-compose.yml and use the appropriate runner. Add to CLAUDE.md template: if project has .venv use .venv/bin/python, if Docker use docker-compose exec. This prevents agents wasting iterations trying bare python3 which has no packages. `type:code_generate` `p2`
- [x] **T080** Add project environment detection to agents: at task start, check project root for docker-compose.yml, .venv/, venv/, package.json, Pipfile, pyproject.toml. Store detected environment in session context. Use this to skip runtime test execution when Docker is not running, and prefer syntax-only verification (py_compile, node --check) instead. Add environment: docker|venv|node|unknown field to .orchid.yaml that overrides auto-detection. `type:code_generate` `p2`
- [x] **T081** Add verify_syntax_only mode to agents: when agents.verify_syntax_only: true is set in .orchid.yaml, DeveloperAgent skips all runtime test execution (pytest, jest, make test, docker exec) and only runs syntax checks (py_compile, node --check, tsc --noEmit). Add this setting to orchid.defaults.yaml defaulting to false. Update agent system prompt to include the current verify mode so the model knows not to attempt runtime tests. `type:code_generate` `p2`
- [x] **T082** Add TesterAgent: new agent class orchid/agents/tester.py focused solely on verification. Detects project environment (docker-compose.yml → docker, .venv/ → venv, package.json → node). Knows how to run: pytest, jest, make test, docker compose exec. Auto-injected by orchestrator after each code_generate task completes using the task manifest file list. Returns structured result: {passed: bool, tests_run: int, failures: [], files_checked: []}. Route type:verify tasks to TesterAgent. `type:code_generate` `p2`
- [x] **T083** Add auto-verify task injection to orchestrator: after a code_generate task completes successfully, automatically create and queue a paired type:verify task targeting the files in the task manifest. The verify task inherits the same priority, is inserted next in queue, and is routed to TesterAgent. Add auto_verify: true/false to orchid.defaults.yaml (default false, opt-in per project via .orchid.yaml). `type:code_generate` `p2`
- [x] **T077** Update README.md and docs/getting-started.md for new features: --run-task flag, SKIP task status ([~]), Active/Inactive project grouping, Project Config tab, Planning tab Discussion history, orchid serve --bots/--telegram/--slack flags (already partially documented but needs the new UI features added) `type:draft` `p2`
- [x] **T078** Update CLAUDE.md hot memory: reflect current state — 446+ tests passing, V2.1 complete, new CLI flags (--run-task, task skip), active/inactive projects in .orchid.yaml, SKIP task status syntax [~] in tasks.md, Discussion history tab in Planning UI `type:draft` `p2`
- [x] **T076** Planning tab Discussion tab: added Discussion tab to ArtifactPanel alongside artifact tabs. Loads from existing GET /api/projects/{id}/discussion endpoint, renders chat-style bubbles (same CSS as DiscussionPanel) in a scrollable read-only view. `type:code_generate` `p2`
- [x] **T070** Add Project Settings panel to Web UI: 'Config' tab shows read-only .orchid.yaml and .env (sensitive values redacted). GET /api/projects/{id}/settings endpoint. `type:code_generate` `p2`
- [x] **T071** Add Active/Inactive project grouping to Web UI Projects panel: expandable Active/Inactive folders with ⏸/▶ toggle. Stored in .orchid.yaml active:true/false. Telegram and Slack bots filter to active-only projects. `type:code_generate` `p2`
- [x] **T064** Fix --log-level flag: convert input to lowercase before passing to uvicorn. uvicorn expects lowercase log levels (debug, info, warning) but users may type DEBUG, INFO etc. Add .lower() to the log_level parameter before passing to uvicorn.run() `type:code_generate` `p2`
- [x] **T065** test task from central slack bot `type:draft` `p2`
- [x] **T066** Update README.md to document V2.1 central bot architecture: orchid serve --bots/--telegram/--slack flags, deprecation of orchid telegram and orchid slack commands, Telegram underscore commands (/orchid_projects, /orchid_switch etc), Slack hyphen commands (/orchid-status, /orchid-projects etc), channel routing, slack-channels.json and telegram-state.json state files `type:draft` `p2`
- [x] **T067** Update CLAUDE.md to reflect current project state: 446 tests passing, V2.1 complete, central bot architecture, update Not yet built section, update CLI reference with all new commands `type:draft` `p2`
- [x] **T068** Update orchid-serve.service.template to include --bots flag and document TELEGRAM_BOT_TOKEN and SLACK_BOT_TOKEN environment variables needed `type:draft` `p2`
- [x] **T063** Add /orchid-unlink-channel Slack command: removes the current channel from slack-channels.json so it can be relinked to a different project. `type:code_generate` `p2`
- [x] **T061** Fix Slack auto-channel creation: added _ensure_channels_for_all_projects() called in start() to create channels for projects that existed before bot startup. `type:code_generate` `p2`
- [x] **T060** Add agent instruction to CLAUDE.md and system prompt: when asked to ADD content to an existing file (README, docs, etc) use append_file not write_file. Only use write_file when the task explicitly says to replace/rewrite the entire file. `type:code_generate` `p2`
- [x] **T055** Fix local KV cache hit detection: change absolute tok/ms threshold to relative ms/tok threshold (<1.0ms per token = cache hit). Add rolling average tracking for better calibration per model. `type:code_generate` `p2`
- [x] **T054** Fix test_duckduckgo_backend_returns_results in tests/test_search.py — DDG HTML scraping is unreliable in CI/automated environments. Mark test with @pytest.mark.skip(reason='DDG scraping unreliable in automated environments') or make it conditional on a ORCHID_NETWORK_TESTS=true env var. `type:code_generate` `p2`
- [x] **T043** Add auto-review config to orchid.defaults.yaml: when auto_review.enabled is true, after every N code_generate tasks automatically insert a review task that runs check_imports and syntax verification on all files written in the previous N tasks. Default: auto_review.enabled=false, auto_review.after_n_tasks=3 `type:code_generate` `p2`
- [x] **T044** Add project_context() tool that reads package.json (JS projects) or pyproject.toml/setup.py (Python projects) and extracts: module system (esm/commonjs), main framework, language, test framework. Inject this into agent context at task start so agents automatically use correct import syntax for the project. `type:code_generate` `p2`
- [x] **T045** Add file manifest to task completion: when an agent marks a task done append files_created and files_modified lists to the task result in session log. Subsequent tasks can query this manifest via a new tool get_task_files(task_id) to know exact filenames created by previous tasks rather than guessing. `type:code_generate` `p2`
- [x] **T039** Add --model flag to --add-task CLI command so users can specify model:claude|local|auto without embedding it in the task title string `type:code_generate` `p2`
- [x] **T037** Create scripts/deploy.sh — one-command deploy script that: 1) builds React frontend (npm run build in orchid/interfaces/web_ui/), 2) reinstalls orchid globally (uv tool install . --force), 3) restarts orchid-serve systemd service (sudo systemctl restart orchid-serve), 4) tails logs for 5 seconds to confirm clean startup. Add usage instructions as comments at top of script. `type:code_generate` `p2`
- [x] **T034** Fix orchid task done subcommand - should not require TITLE argument when --id is provided `type:code_generate` `p2`
- [x] **T030** Test CLI --help option `type:draft` `p2`
- [x] **T027** test task from Slack `type:draft` `p2`
- [x] **T028** Fix Slack formatter: hot memory code blocks missing closing triple backtick in Slack messages `type:draft` `p2`
- [x] **T019** Add task archiving - completed tasks older than N days move to tasks.md archive section to keep board clean `type:code_generate` `p2`
- [x] **T020** Add orchid telegram systemd service install script to scripts/ `type:code_generate` `p2`
- [x] **T016** test task from Telegram `type:draft` `p2`
- [x] **T015** test task from Telegram `type:draft` `p2`
- [x] **T013** Fix CLAUDE.md compression truncating decision entries `type:code_generate` `p2`
- [x] **T009** Fix orchid task add subcommand - unexpected extra argument error `type:code_generate` `p2`
- [x] **T003** Preserve prior summary on re-compression `type:code_generate` `p2`
- [x] **T004** Add multi-cycle compression tests `type:code_generate` `p2`
- [x] **T005** Document _save() contract in docstring `type:draft` `p3`
- [x] **T006** Wire context window size to orchid.defaults.yaml `type:code_generate` `p3`
