# Tasks


## DONE

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
