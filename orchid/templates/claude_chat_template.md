I use Orchid — a self-hosted AI agent orchestration framework running on 
my AI server (bare-metal Ubuntu, 128GB RAM, AMD GPU/ROCm). Orchid manages 
projects as independent git repos with CLAUDE.md, tasks.md, and .orchid.yaml.

I want to start a new project and need help designing it and generating 
an Orchid kickoff package.

Please help me:
1. Clarify and scope the project idea
2. Design the architecture and tech stack
3. Generate the complete Orchid kickoff package:
   - CLAUDE.md content (project overview, architecture notes, key decisions)
   - tasks.md with 8-15 well-scoped tasks covering research, code, docs
     in the correct Orchid format:
     - [ ] **T001** Task title `type:code_generate` `p1` `model:claude`
     Task types: code_generate, draft, review, research, plan, critique
     Priorities: p1 (high), p2 (normal), p3 (low)
     Models: claude, local, auto
   - .orchid.yaml with appropriate agent roles and model preferences
   - Recommended orchid serve watch directory if outside ~/LocalAI

## My Orchid setup
- Claude API (Anthropic) for orchestration, review, planning
- Local llama.cpp on localhost:8080 for code generation and drafts
- Ollama available as alternative local provider
- Embedding server on localhost:8081 (nomic-embed-text)
- Web UI at orchid.scheidy.com
- Telegram and Slack bots for notifications
- Two watch directories: ~/LocalAI and ~/Documents/Development

## Model routing guidance
- Use model:claude for: complex logic, regex, auth, security, algorithms
- Use model:local for: simple code, README, boilerplate, summaries
- Use model:auto for: general tasks where complexity is uncertain

## Project idea
[DESCRIBE YOUR PROJECT HERE]