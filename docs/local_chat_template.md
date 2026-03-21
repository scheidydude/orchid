I use Orchid — a self-hosted AI agent orchestration framework running on 
my AI server (bare-metal Ubuntu, 128GB RAM, AMD GPU/ROCm). Orchid manages 
projects as independent git repos with CLAUDE.md, tasks.md, and .orchid.yaml.

I want to start a new project and need help designing it and generating 
an Orchid kickoff package. This project will run entirely on my local 
LLM infrastructure — no cloud API calls.

Please help me:
1. Clarify and scope the project idea
2. Design the architecture and tech stack
3. Generate the complete Orchid kickoff package:
   - CLAUDE.md content (project overview, architecture notes, key decisions)
   - tasks.md with 8-15 well-scoped tasks covering research, code, docs
     in the correct Orchid format:
     - [ ] **T001** Task title `type:code_generate` `p1` `model:local`
     Task types: code_generate, draft, review, research, plan, critique
     Priorities: p1 (high), p2 (normal), p3 (low)
     ALL tasks must use model:local — no model:claude entries
   - .orchid.yaml configured for fully local execution
   - Recommended orchid serve watch directory if outside ~/LocalAI

## My Orchid setup
- Local llama.cpp on localhost:8080 — primary model for all tasks
- Ollama available as alternative local provider
- Embedding server on localhost:8081 (nomic-embed-text)
- NO Claude API or other cloud providers for this project
- Web UI at orchid.scheidy.com
- Telegram and Slack bots for notifications
- Two watch directories: ~/LocalAI and ~/Documents/Development

## .orchid.yaml for local-only projects
The generated .orchid.yaml must set:
  model_preference: local
  providers:
    orchestrator: local
    developer: local
    researcher: local
    reviewer: local
    base: local

## Task design guidance for local models
Local LLMs perform best on well-scoped, concrete tasks. When designing
the task list please:
- Break complex tasks into smaller focused subtasks (max 2-3 files per task)
- Avoid tasks requiring complex regex, auth, or multi-file refactors in one shot
- Prefer tasks with explicit acceptance criteria the model can verify
- For research tasks: provide specific search queries rather than open-ended topics
- For code tasks: specify the exact file, function name, and expected behavior
- Add a review task at the end of each logical group (reviewer agent uses local too)
- Keep task descriptions under 100 words — local models do better with focused prompts

## Offline / air-gap mode
This project is designed to run with:
  orchid --project . --mode auto --offline
or persistently via orchid serve with no cloud dependencies.

## Project idea
[DESCRIBE YOUR PROJECT HERE]