---
name: Orchid Code Index
description: Full index of all Python files, classes, and key functions in the orchid/ package — use this before reading files to know where to look
type: reference
---

# Orchid Code Index
_Last updated: 2026-05-03 (T092–T104 gap fixes)_

## Core Framework

### orchid/__init__.py
- `__version__` — package version string

### orchid/config.py
- `_expand_env(value)` — expand ${VAR:-default} in config
- `_deep_merge(base, override)` — recursive config dict merge
- `_load_yaml(path)` — safe YAML load
- `load_defaults()` — load bundled orchid.defaults.yaml
- `load_project_config(project_dir)` — load .orchid.yaml
- `merge_for_project(project_dir)` — merge defaults + project overrides
- `configure_for_project(project_dir)` — set global config
- `get_config()` — get current global config
- `get(key_path, default)` — dot-path key lookup

### orchid/orchestrator.py
- `_get_registry()` — get agent class registry
- `_AGENT_REGISTRY` — agent type → class mapping
- **`TraceWriter`** — writes ReAct traces to .orchid/trace.log
  - `task_start(task_id, title)`
  - `iteration(...)`
  - `task_summary(...)`
- **`Orchestrator`** — top-level orchestration loop
  - `run_once()` — pick and execute one task
  - `run_loop(max_tasks)` — run until no tasks remain
  - `_execute_task(task)` — core dispatch with provider routing
  - `_fire_task_start_hook(task, model)`
  - `_fire_task_complete_hook(task, result, files_written)`
  - `_fire_task_failed_hook(task, error)`
  - `_make_stream_callback(task_id, task_title)`
  - `_plan_task(task)` — generate plan via Claude
  - `_resolve_agent(task)` — resolve agent class
  - `_execute_rollup_task(task)` — synthesize multi-task results
  - `_insert_auto_review_task()` — inject auto-review (T043)
  - `_insert_auto_verify_task(source_task, files)` — inject verify (T083)
  - `_write_task_metrics(...)`

### orchid/session.py
- `get_current_session()` — get Session singleton
- **`Session`** — project state for one orchestrator run
  - `load()` — read all state from disk
  - `save()` — persist mutated state
  - `close(summary)` — save + write session log + embed
  - `_fire_session_start_hook()`
  - `_fire_session_end_hook(summary)`
  - `_load_context_files()`
  - `_maybe_compress_hot_memory()`
  - `_write_session_log(summary)`
  - `_finalize_live_log()`
  - `_auto_embed_session(summary)`
  - `next_task()` — get next runnable task
  - `update_task_status(task_id, status)`
  - `context_block()` — return context string
  - `stream_react(data)` — log ReAct stream to live log
  - `log_event(type, data)`
  - `record_delegation(record)`

### orchid/runner.py
- **`_ProjectState`** — per-project run state dataclass
- **`BackgroundRunner`** — manages auto-runs for multiple projects
  - `start(project_path)`
  - `stop(project_path)`
  - `get_status(project_path)`
  - `_run(project_path, state)`

### orchid/lifecycle.py
- `PHASES` — valid phase names list
- `_VALID_TRANSITIONS` — allowed phase transitions
- **`ProjectState`** — dataclass: phase, name, gates, artifacts
- **`ProjectLifecycle`** — phase state machine
  - `load()` — load from .orchid/project.state.json
  - `save()`
  - `current_phase()`
  - `advance(phase)` — validate + transition
  - `_fire_phase_exit_hook(current, next)`
  - `_fire_phase_enter_hook(next)`

### orchid/errors.py
- `OrchidError` — base exception
- `ProviderError(OrchidError)` — provider error
- `ToolError(OrchidError)` — tool error

---

## Agent System — orchid/agents/

### base.py
- `_BUILTIN_TOOLS` — built-in tool registry
- `_make_project_tools(project_dir)` — filesystem tools anchored to project
- `_get_task_files_for_project(task_id, project_dir)`
- **`BaseAgent`** — ReAct loop (Reason→Act→Observe)
  - `run(task)` — main agent loop
  - `system_prompt()` — build system prompt
  - `register_tool(name, fn)`
  - `_parse_action(text)` — parse action from LLM output
  - `_call_tool(name, input_str)`
  - `_make_messages(context)`

### developer.py — `DeveloperAgent(BaseAgent)` — code writing
### researcher.py — `ResearcherAgent(BaseAgent)` — web search + summarize
### reviewer.py — `ReviewerAgent(BaseAgent)` — critic + quality gate
### tester.py — `TesterAgent(BaseAgent)` — verification only, no code write
### product_manager.py — `ProductManagerAgent` — generates REQUIREMENTS.md + ARCHITECTURE.md
### project_manager.py — `ProjectManagerAgent` — generates MILESTONES.md + tasks.md

### discussion_agent.py
- `_extract_tag(text, tag)` — extract XML-style tag content
- `_extract_suggestions(text)` — extract follow-up suggestions
- **`DiscussionResponse`** — dataclass: reply, readiness, context_updates, suggestions
- **`DiscussionAgent`** — conversational requirements capture
  - `chat(user_message, status_callback)`
  - `should_advance()`

### delegator.py
- `_get_agent_class(agent_type)` — type string → class
- **`AgentDelegator`** — agent-to-agent delegation
  - `delegate(agent_type, task, context, depth, parent_agent)`

---

## Memory — orchid/memory/

### state.py
- `load_tasks(project_dir)` — parse tasks.md → Task objects. Uses `_TASK_LINE_RE` + `_META_BOUNDARY_RE` to split title from metadata — title can contain backticks safely
- `save_tasks(tasks, project_dir)` — write tasks.md
- `load_hot_memory(project_dir)` — load CLAUDE.md hot memory
- `save_hot_memory(text, project_dir)`
- **`TaskStatus`** — enum: TODO/IN_PROGRESS/DONE/BLOCKED/CANCELLED/SKIPPED
- **`Task`** — task dataclass
  - `is_runnable(completed_ids)`
  - `to_md_line()`
  - `_status_char()`
- **`TaskResultStore`** — persists results to .orchid/results.jsonl
  - `append(task_id, title, type, result)`
  - `get_many(task_ids)`
  - `get(task_id)`

### decisions.py
- `load_decisions(project_dir)`
- `record_decision(title, decision, rationale, context, project_dir)`
- `recent_decisions(n, project_dir)`
- `decisions_as_md(n, project_dir)`

### vector.py
- `_count_tokens(text)` — BPE token count
- `_chunk_text(text, chunk_size, overlap)` — sliding window chunker
- **`VectorMemory`** — ChromaDB-backed semantic memory
  - `add(text, metadata, doc_id_prefix)`
  - `query(text, n)`
  - `query_by_id(doc_id)`
  - `delete_by_prefix(prefix)`
  - `available` — property: is ChromaDB available?

---

## Hooks — orchid/hooks/

### events.py
- Event string constants (22 total):
  - Agent ReAct: `AGENT_ITER_START`, `AGENT_ITER_END`, `AGENT_ACTION`, `AGENT_OBSERVATION`, `AGENT_THOUGHT`, `AGENT_FINAL_ANSWER`
  - Tool use: `PRE_TOOL_USE`, `POST_TOOL_USE` — wired into BaseAgent before/after every `_dispatch()` call
  - Delegation: `DELEGATION_START`, `DELEGATION_END`
  - Task: `TASK_START`, `TASK_END`, `TASK_COMPLETE`, `TASK_FAILED`, `TASK_BLOCKED`, `TASK_SKIPPED`, `TASK_STATUS_CHANGE`
  - Session/Phase: `SESSION_START`, `SESSION_END`, `PHASE_TRANSITION`, `PHASE_ENTER`, `PHASE_EXIT`
  - System: `HOOK_REGISTERED`, `HOOK_UNREGISTERED`, `HOOK_ERROR`
- **`HookEvent`** — dataclass: event_type, data, context
  - `pre_tool_use_event(tool, input_data, task_id)`, `post_tool_use_event(tool, input_data, output, task_id)`
  - `delegation_start_event(...)`, `delegation_end_event(...)`
- Typed context dataclasses: `PreToolUseContext`, `PostToolUseContext`, `TaskStartContext`, `TaskEndContext`, `SessionStartContext`, `SessionEndContext`, `PhaseTransitionContext`, `DelegationContext`

### registry.py
- **`HookResult`** — dataclass: `blocked: bool`, `mutated_context: dict|None`, `error: str|None`, `results: list`
- **`HookHandler`** — dataclass: handler metadata
- **`HookRegistry`** — central event dispatch
  - `register(event_type, handler, priority, mode, timeout)`
  - `unregister(handler_id)`
  - `fire(event, ignore_errors)` → `HookResult` (blocked=True if any sync handler returns `{"blocked": True}`)

### loader.py
- **`HookLoadError(Exception)`** — raised when hooks.py import fails
- **`HookLoader`** — loads hooks from .orchid.yaml + project hooks.py
  - `load()` — loads yaml config sections + calls `_load_project_hooks_py()`
  - `_load_section(hooks, category)`
  - `_load_project_hooks_py()` — imports hooks.py from project root, registers `@orchid_hook`-decorated functions
  - `_parse_shell_hook(...)`, `_parse_http_hook(...)`, `_parse_python_hook(...)`
  - `_create_shell_handler(hook)` — shell cmd receives context JSON on stdin; stdout `{"block":true}` → blocked signal

### types.py
- **`HookCategory`** — enum: hook categories
- **`HookExecutionMode`** — enum: sync/async/background
- **`ShellHook`**, **`HTTPHook`**, **`PythonHook`** — per-type dataclasses

### schema.py
- `validate_hooks_config(config)`
- `validate_hook(hook_spec)`
- `get_schema_documentation()`
- **`HooksConfigSchema`**, **`ShellHookSchema`**, **`HTTPHookSchema`**, **`PythonHookSchema`** — Pydantic schemas

---

## Gates — orchid/gates.py
- **`GateStatus`** — enum: OPEN/WAITING/BLOCKED
- **`GateSystem`** — human-in-the-loop phase transition control
  - `check_gate(to_phase)`
  - `approve(to_phase, approver)`
  - `notify_gate_reached(to_phase)`
  - `_fire_gate_approved_hook(...)`

---

## Providers — orchid/providers/

### base.py
- **`ProviderUnavailableError(Exception)`**
- **`ProviderBase(ABC)`** — abstract model provider
  - `is_available()` — cached availability check
  - `reset_availability_cache()`
  - `_check_availability()` — subclass implements
  - `complete(messages, system)` — abstract
  - `embed(text)` — optional
  - `optimize_for_caching(stable_parts, dynamic_parts)`

### anthropic.py — `AnthropicProvider(ProviderBase)` — Claude API with prompt caching
- `get_session_stats()`, `reset_session_stats()` — cache stats
- `complete(messages, system, ...)`, `optimize_for_caching(...)`

### local.py — `LocalProvider(ProviderBase)` — llama.cpp OpenAI-compat endpoint
### openai.py — `OpenAIProvider(ProviderBase)`, `OpenRouterProvider(OpenAIProvider)`
### ollama.py — `OllamaProvider(ProviderBase)` — local Ollama inference
### bedrock.py — `BedrockProvider(ProviderBase)` — AWS Bedrock

### registry.py
- `get_registry()` — get ProviderRegistry singleton
- `reset_registry()` — force reload
- **`ProviderRegistry`** — 5-layer provider resolution
  - `load()`
  - `resolve(agent_type, agent_name, task_type, ...)` → ProviderBase
  - `resolve_name(...)` → str
  - `get_by_key(model_key)`

---

## Tools — orchid/tools/

### models.py — unified model caller routing via ProviderRegistry
- **`RouteDecision`** — dataclass: model, reason, source
- **`Message`** — simple message wrapper
- `call(messages, model_key, system, ...)` — call provider
- `embed(text)` — get embedding vector
- `reset_embed_cache()`

### filesystem.py
- `read_file(path)`, `write_file(path, content)`, `append_file(path, content)`
- `list_dir(path)`, `file_exists(path)`

### shell.py — sandboxed bash execution
- `detect_python_runner(project_path)`
- `detect_environment(project_path)` — docker/venv/node/python/unknown
- `rewrite_python_command(command, project_path)`
- `bash(command, timeout)`
- `warn_insecure_command(command)`

### search.py — SearXNG/Brave/DuckDuckGo + trafilatura extraction
- `SearXNGBackend`, `BraveBackend`, `DuckDuckGoBackend`
- **`WebSearchTool`**
  - `search_and_format(query)`
  - `fetch_page(url)`

### consistency.py — scan for broken relative imports
- `check_imports(project_path)`
- `check_imports_summary(project_path)`

### project_context.py — detect language/framework/test framework
- **`ProjectContext`** — `to_dict()`, `to_context_block()`
- **`ProjectContextTool`** — `project_context()`

### auto_review.py
- `run_auto_review(project_path, files)`
- `_check_syntax(path, suffix)`

### retry.py — httpx with tenacity
- **`RetryConfig`**, **`RetryClient`**

### session_log_parser.py — extract structured data from session logs
- **`SessionLogEvent`** — parsed event dataclass

---

## Planning & Project Creation

### orchid/planning.py
- **`PlanningSession`** — conversational planning
  - `chat(user_message, status_callback)`
  - `_load_history()`, `_save_history()`
  - `get_history()`

### orchid/discussion.py
- **`DiscussionHistory`** — persistent conversation log
  - `load()`, `append(role, message, phase)`, `update_context(updates)`
  - `get_full_history()`, `get_recent(n)`, `turn_count()`
  - `get_context_md()`

### orchid/project_creator.py
- **`ProjectCreator`** — creates new orchid-managed projects
  - `confirm_path(name, project_type)`
  - `create(name, description, project_type, base_dir, git_init)`
  - `_apply_templates(project_dir, name, description)`

---

## Multi-Project & Parallel Execution

### orchid/multi.py
- `worker_main(project_path, notification_queue, ...)` — worker process entry
- `_install_semaphore_wrapper(api_sem, local_sem)`
- `_create_semaphore(max_concurrent)`
- **`MultiOrchid`** — multi-project coordinator
  - `run(projects, max_concurrent_api, ...)`
  - `_monitor_workers(...)`
  - `_drain_notification_queue()`

### orchid/agent_manager.py
- **`ProjectConfig`** — per-project config dataclass
- **`AgentManager`** — per-project auto-runs + APScheduler
  - `start()`, `stop()`, `trigger(project_id)`, `get_status(project_id)`
  - `_start_scheduler(projects)`, `_run_project(project_id)`

---

## Discovery

### orchid/discovery.py
- **`ProjectDiscovery`** — filesystem scan + watchdog monitoring
  - `is_orchid_project(path)`
  - `scan()`
  - `_scan_dir(directory, current_depth, found)`
  - `watch(on_changed, on_removed)`

---

## Machine Profile

### orchid/machine_profile.py
- **`MachineProfile`** — developer machine configuration
  - `load(path)` — load ~/.config/orchid/machine-profile.yaml
  - `save()`
  - `get_project_root(project_type)`

---

## Interfaces — orchid/interfaces/

### cli.py (Typer)
- `_version_callback(value)`, `_setup_logging(level)`, `_make_session(project)`, `_resolve_project(project)`
- Commands: `main`, `init`, `decide`, `discuss`, `plan`, `execute`, `list_tasks`, `update_task`, `web`, `serve`, `telegram`, `slack`

### web_server.py (FastAPI)
- **`ConnectionManager`** — WebSocket manager
- **`_WebProjectRunner`** — runs orchestrator in thread pool
- `create_app(project_paths, watch_dirs, ...)` — factory
- `_register_project(path)`, `_unregister_project(path)`
- Routes: GET /api/projects, GET /api/projects/{id}/status, POST /api/projects/{id}/start, POST /api/projects/{id}/stop, WS /ws/{id}

### telegram_bot.py — **`TelegramBot`** — polling bot; /status /run /auto /stop /add_task
### slack_bot.py — **`SlackBot`** — Socket Mode; @mention + DM handling
### background_runner.py — **`BackgroundRunner`** — orchestrator in background thread
### slack_formatter.py — format data for Slack
### telegram_formatter.py — format data for Telegram
### slack_central.py — **`CentralSlackBot`** — multi-project Slack
### telegram_central.py — **`CentralTelegramBot`** — multi-project Telegram
### central_bot.py — base class for central bots
### multi_formatter.py — format multi-project updates
### hooks_cli.py — CLI commands: list/show/validate/test/stats/add/remove hooks

---

## Tests — tests/

| File | Coverage |
|------|----------|
| test_agents.py | Agent functionality |
| test_config.py | Config loading/merging |
| test_delegator.py | Agent delegation |
| test_discussion.py | Conversational requirements |
| test_discovery.py | Project discovery |
| test_gates.py | Gate system |
| test_hooks.py | Hook events, types, schema tests |
| test_hooks_registry.py | HookRegistry + HookResult blocking/propagation |
| test_hooks_loader.py | HookLoader shell stdin/stdout blocking + hooks.py decorator |
| test_hooks_integration.py | Agent PRE_TOOL_USE blocking + TASK_COMPLETE log hook |
| test_integration.py | End-to-end scenarios |
| test_lifecycle.py | Phase transitions |
| test_machine_profile.py | Machine profile |
| test_memory.py | State management |
| test_multi.py | Multi-project execution |
| test_orchestrator.py | Orchestration loop |
| test_planning.py | Planning generation |
| test_project_creator.py | Project creation |
| test_providers.py | Provider registry |
| test_routing.py | Task routing logic |
| test_session.py | Session lifecycle |
| test_shell.py | Shell sandboxing |
| test_state.py | Task state management |
| test_tools.py | Filesystem/search/consistency tools |
| test_vector_memory.py | Vector memory |
| test_web.py | Web server API |
| test_web_v2.py | Web server WebSocket |

---

## Stats
- **72** Python files in orchid/
- **7** agent types
- **6** providers
- **18** hook event types
- **9** tool modules
- **446+** tests passing
