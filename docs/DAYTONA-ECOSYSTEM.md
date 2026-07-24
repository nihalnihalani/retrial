# Open-source projects built on Daytona — research notes

> ARCHIVED pre-pivot research (the "Fork Wars" concept, superseded by
> WINNING-IDEA.md / the shipped Retrial engine). Sections describing sponsor
> UI-framework integrations that were never built have been removed — this
> file keeps only the Daytona-ecosystem facts that remain useful reference.
> Nothing here is a claim about what Retrial integrates; see SPONSORS.md for
> that list.

## ⭐ VibeKit (github.com/daytonaio/vibekit) — official Daytona
**What:** TypeScript SDK to run coding agents (Claude Code, Codex, Gemini CLI, SST OpenCode) inside secure sandboxes. `npx @vibe-kit/sdk init`.
**Why it mattered as reference:** it is a per-lane agent engine off the shelf —
- Runs a coding agent inside a Daytona sandbox (each race lane = one VibeKit agent)
- **Real-time output streaming for UI**
- **GitHub automation: branches, commits, PRs**
- Provider-agnostic (E2B, Daytona, Northflank, Cloudflare, Dagger) — set backend = Daytona.

(Not adopted: Retrial's trial runner is a single exec round-trip, no agent loop needed.)

## ⭐ SWE-ReX (github.com/daytonaio/SWE-ReX) — official Daytona
**What:** `pip install swe-rex`. Runtime interface for running _any command_ on _any environment_; agent code stays identical whether local/Docker/AWS/Modal/Daytona (Daytona backend WIP).
**Why relevant:** it's the **massively-parallel** primitive — demoed running **30 concurrent SWE-bench instances**. Proves the "N isolated runs racing in parallel" pattern at scale and gives a reference implementation for parallel session management; a strong Q&A/architecture reference ("this is the pattern SWE-agent uses for parallel eval").

## Daytona's OWN framing = the fork engine, verbatim
Daytona markets forking as: *"forking sandbox state into parallel branches, enabling multi-agent exploration where different reasoning paths need isolated but identically initialized environments — particularly relevant for **best-of-N code generation** where you explore multiple solution paths in parallel."* That is exactly the argument behind `RETRIAL_POOL_BACKEND=fork`: byte-identical initial state per trial. Use their language back at the Daytona judges.

## Other official Daytona repos worth a look
- **vibekit** (above) · **SWE-ReX** (above)
- **inngest-agentkit-coding-agent** (archived) — AgentKit coding agent powered by Daytona (reference for agent loop)
- **langchain-daytona** / `langchain_daytona_data_analysis` — `DaytonaSandbox` backend for deepagents + `DaytonaDataAnalysisTool` (pip `langchain-daytona`)
- **daytona-adk-plugin** (archived) — Google ADK plugin
- **composio** — Daytona in the Composio integration framework
- **ai-enablement-stack** (630★) — community map of AI dev tools

## Official examples for the "new features"
- **OpenAI Cookbook — "Computer Use Agents in Daytona Sandboxes"** (developers.openai.com): uses a Daytona sandbox as the desktop for the OpenAI Agents SDK computer-use tool (VNC Linux desktop + browser via Python SDK). Reference if we ever demo computer-use.
- **Vercel AI SDK `ToolLoopAgent` + Daytona**: multi-language benchmark agents — reference for a Fireworks-model-via-AI-SDK lane driver.
- **Real adopters:** LangChain, Turing, Writer, SambaNova run Daytona sandboxes in production (credibility name-drops for the pitch).
