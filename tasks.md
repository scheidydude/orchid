# Tasks


## TODO

- [ ] **T060** Add agent instruction to CLAUDE.md and system prompt: when asked to ADD content to an existing file (README, docs, etc) use append_file not write_file. Only use write_file when the task explicitly says to replace/rewrite the entire file. `type:code_generate` `p2`

## DONE

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
