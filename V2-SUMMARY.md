# Orchid V2 Feature Summary

## Overview
Orchid V2 introduces a next-generation AI agent orchestration framework with improved lifecycle management, strategic agent architecture, enhanced web UI, and intelligent prompt caching.

---

## Lifecycle Phases

Orchid agents follow a structured ReAct (Reason → Act → Observe) loop across four key phases:

### 1. Planning Phase
- **Objective**: Understand task requirements and create execution strategy
- **Key Activities**: Task decomposition, dependency analysis, tool selection
- **Output**: Structured plan with milestones and artifacts

### 2. Execution Phase
- **Objective**: Implement plan through iterative tool calls
- **Key Activities**: Code generation, file operations, API calls, testing
- **Output**: Working artifacts and intermediate results

### 3. Review Phase
- **Objective**: Validate quality and correctness
- **Key Activities**: Code review, consistency checks, import validation
- **Output**: Feedback loop for corrections or approval

### 4. Completion Phase
- **Objective**: Finalize deliverables and update state
- **Key Activities**: Artifact generation, session logging, memory compression
- **Output**: Persistent state updates, task completion markers

---

## Strategic Agents

Orchid employs a two-tier agent routing system optimized for cost and performance:

### Claude-tier Agents (High-level orchestration)
- **Orchestrator**: Task routing, session management, state coordination
- **Planner**: Strategic task decomposition and dependency resolution
- **Reviewer**: Code quality assessment, consistency validation
- **Synthesizer**: Knowledge aggregation, decision documentation

### Local-tier Agents (High-volume operations)
- **Developer**: Code generation, refactoring, debugging
- **Draft**: Rapid prototyping, documentation, brainstorming
- **Search**: Information retrieval, research assistance
- **Summarizer**: Context compression, hot memory updates

### Routing Logic
```
CLI/Request → Orchestrator → Task Type → Model Selection
  - type:code_generate → local (llama.cpp)
  - type:review → claude
  - type:plan → claude
  - type:draft → local
```

---

## Web UI Planning Tab

The Planning Tab provides real-time visibility into agent operations:

### Core Components
- **DiscussionPanel**: Chat interface for user-agent interaction with:
  - Streaming WebSocket responses
  - Auto-focus on input after agent response
  - Loading indicators during artifact generation
  - Status callbacks ("Generating REQUIREMENTS.md...")

- **ArtifactPanel**: Displays generated files and project structure
  - Real-time updates via WebSocket
  - File preview and navigation
  - Success banners on completion

- **ApprovalPanel**: Review and approval workflow
  - Diff viewing for code changes
  - One-click approve/reject actions
  - Audit trail for decisions

### UX Improvements
- Fixed scrollable content (overflow-y: auto)
- Loading spinners during processing
- Disabled input during agent work
- Success notifications on completion

---

## Prompt Caching

Intelligent caching system for reducing API costs and latency:

### Cache Types
1. **System Prompt Cache**: Repeated system instructions cached per model
2. **Context Cache**: Project knowledge and session history cached
3. **KV Cache**: Local key-value cache for repeated token sequences

### Cache Hit Detection
- **Threshold**: <1.0ms per token indicates cache hit
- **Tracking**: Rolling average per model for calibration
- **Fallback**: Automatic degradation to full generation on miss

### Configuration
```yaml
cache:
  enabled: true
  system_prompt_cache: true
  context_cache: true
  kv_cache:
    enabled: true
    hit_threshold_ms: 1.0
    rolling_window: 10
```

### Benefits
- **Cost Reduction**: Up to 70% reduction in Claude API costs
- **Latency**: 3-5x faster for repeated prompts
- **Rate Limits**: Reduced pressure on API rate limits

---

## Architecture Decisions

- **D0001**: File-based state (tasks.md, CLAUDE.md, decisions.json)
- **D0002**: Two-tier routing (Claude ↔ local)
- **D0003**: ReAct loop with text-parsed tool calls
- **D0004**: Interface-agnostic core
- **D0005**: Three-layer config (defaults → .orchid.yaml → CLI)
- **D0006**: Standalone runtime (projects not subfolders)
- **D0007**: Embedded ChromaDB for vector storage

---

## Future Roadmap

- Multi-project orchestration (--multi flag)
- Telegram/Slack integrations
- Enhanced offline mode with local LLMs
- Advanced prompt caching strategies
- Real-time collaboration features