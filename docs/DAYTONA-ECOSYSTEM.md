# Open-source projects built on Daytona — steal-worthy for Fork Wars

## ⭐ TOP REUSE: VibeKit (github.com/daytonaio/vibekit) — official Daytona
**What:** TypeScript SDK to run coding agents (Claude Code, Codex, Gemini CLI, SST OpenCode) inside secure sandboxes. `npx @vibe-kit/sdk init`.
**Why it's a gift for Fork Wars:** it is literally our per-lane engine off the shelf —
- Runs a coding agent inside a Daytona sandbox (each race lane = one VibeKit agent)
- **Real-time output streaming for UI** → wire straight into the CopilotKit race-track panel
- **GitHub automation: branches, commits, PRs** → the winning fork's PR is created by VibeKit, then gated by CodeRabbit. Closes our whole loop.
- Provider-agnostic (E2B, Daytona, Northflank, Cloudflare, Dagger) — set backend = Daytona.
**Action:** evaluate at 10:00 as the lane engine; may save hours vs hand-rolling agent-in-sandbox + PR creation. Docs: docs.vibekit.sh.

## ⭐ CONCEPT REUSE: SWE-ReX (github.com/daytonaio/SWE-ReX) — official Daytona
**What:** `pip install swe-rex`. Runtime interface for running _any command_ on _any environment_; agent code stays identical whether local/Docker/AWS/Modal/Daytona (Daytona backend WIP).
**Why relevant:** it's the **massively-parallel** primitive — demoed running **30 concurrent SWE-bench instances**. Proves the "N isolated agents racing in parallel" pattern at scale and gives us a reference implementation for parallel session management. Even if we don't adopt it, it's a strong Q&A/architecture reference ("this is the pattern SWE-agent uses for parallel eval").

## Daytona's OWN framing = Fork Wars, verbatim
Daytona markets forking as: *"forking sandbox state into parallel branches, enabling multi-agent exploration where different reasoning paths need isolated but identically initialized environments — particularly relevant for **best-of-N code generation** where you explore multiple solution paths in parallel."* That is our exact pitch. Use their language back at the 2 Daytona judges: "we built the best-of-N parallel-branch pattern from your own docs."

## Official CopilotKit + Daytona pattern (both are sponsors!)
CopilotKit's **Built-in Agent** wires to a Daytona sandbox with full shell + filesystem access, where **every tool call streams into the chat as generative UI** and **hosted processes embed as live iframes**. → This is the sanctioned way to render each lane's live preview URL *inside* the CopilotKit UI as an iframe, and stream each model's fix attempt as generative UI. Directly serves both the Daytona AND CopilotKit prizes with one integration.

## Other official Daytona repos worth a look
- **vibekit** (above) · **SWE-ReX** (above)
- **inngest-agentkit-coding-agent** (archived) — AgentKit coding agent powered by Daytona (reference for agent loop)
- **langchain-daytona** / `langchain_daytona_data_analysis` — `DaytonaSandbox` backend for deepagents + `DaytonaDataAnalysisTool` (pip `langchain-daytona`)
- **daytona-adk-plugin** (archived) — Google ADK plugin
- **composio** — Daytona in the Composio integration framework
- **ai-enablement-stack** (630★) — community map of AI dev tools

## Official examples for the "new features"
- **OpenAI Cookbook — "Computer Use Agents in Daytona Sandboxes"** (developers.openai.com): uses a Daytona sandbox as the desktop for the OpenAI Agents SDK computer-use tool (VNC Linux desktop + browser via Python SDK). Reference if we ever demo computer-use.
- **Vercel AI SDK `ToolLoopAgent` + Daytona**: multi-language benchmark agents — reference for the Fireworks-model-via-AI-SDK lane driver.
- **Real adopters:** LangChain, Turing, Writer, SambaNova run Daytona sandboxes in production (credibility name-drops for the pitch).

## ⭐⭐ THE DEEP DAYTONA + COPILOTKIT INTEGRATION (official Daytona guide — Fork Wars starter kit)
Guide: daytona.io/docs/en/guides/copilotkit/copilotkit-generative-ui-coding-agent-sandbox. This is a Next.js app where a CopilotKit **Built-in Agent** drives a Daytona sandbox and every tool call renders as a live React card. It is ~80% of Fork Wars' UI+sandbox layer, pre-built and sponsor-blessed.

**Architecture:**
- Backend `app/api/copilotkit/route.ts`: `Daytona` client → `BuiltInAgent(model, prompt, tools[], maxSteps:30)` → `CopilotRuntime({agents:{default}})` → `copilotRuntimeNextJSAppRouterEndpoint()`. Packages: `@copilotkit/react-core/v2`, `@copilotkit/runtime/v2` (`BuiltInAgent`, `defineTool`), `@copilotkit/runtime`, `@daytona/sdk`, `zod`, Next.js App Router.
- **11 sandbox tools** via `defineTool`: createSandbox, runCommand (sync/`background`), writeFile, readFile, listFiles, findFiles, searchFiles, replaceInFiles, getFileDetails, startWebServer, getPreviewUrl.
- Frontend `page.tsx`: each tool gets a `useRenderTool({name, parameters, render})` → typed React card. status flows inProgress→executing→complete; result is a JSON string (needs JSON.parse).
- **Cards:** TerminalCard (runCommand), FileCard (write/readFile, syntax-highlighted), FileListCard, GrepCard, ReplaceCard, FileInfoCard, **PreviewCard** = the key one: skeleton while loading → live `<iframe src={previewUrl}>` embedded in the chat, persists across turns.
- **Live HMR loop:** startWebServer runs `vite --host 0.0.0.0` via `sandbox.process.createSession(runAsync)`, polls logs for readiness, calls `sandbox.getPreviewLink(port)`; the iframe holds a WebSocket to the dev server so `writeFile` → HMR update → in-place reload with NO card re-render. Requires `vite.config: server.hmr={clientPort:443, protocol:'wss'}` to survive the HTTPS proxy.

**How to turn it into the 4-lane race (per the guide's own "multi-lane adaptation" section):**
1. Add a `lane:number` param to createSandbox; store `Map<lane, sandboxId>`.
2. Route each lane's tool calls to its own sandbox; each lane runs its own agent turn concurrently → 4 sandboxes managed at once.
3. Replace the linear chat timeline with a `RaceGrid` of 4 `LanePreviewCard`s side by side (4 iframes), each keyed by lane.
4. Per-lane state `{sandboxId, status: idle|building|ready|error}` → drives a leaderboard / lap-time display.
5. The tool-execution + streaming model is UNCHANGED — only the UI choreography shifts from timeline to grid.

**Why this is the highest-leverage integration in the whole project:**
- Serves BOTH sponsor prizes with one codebase — Daytona (sandbox+preview) AND CopilotKit (generative UI via useRenderTool, NOT a chat sidebar — exactly what the 2 CopilotKit judges probe for).
- The `useRenderTool` cards ARE the "real generative UI" the judges demand; the iframe-in-chat is the money-shot render surface for each lane's live app.
- Fork Wars only has to ADD: the Fireworks brain per lane (the guide uses `openai:gpt-5.4`; swap for 4 Fireworks model IDs — see DAYTONA-COOKBOOK "brain vs hands"), the Braintrust referee, and the CodeRabbit gate. The hardest part (sandbox↔generative-UI plumbing) is done.
- Bug Draft upgrade = a `useCopilotAction`/tool button in this same UI. Crowd Call = another card. All native to this stack.

**Build path:** clone/scaffold this guide first at 10:00, get one lane rendering an iframe, then fan out to 4 lanes + swap in Fireworks. This replaces "hand-roll CopilotKit + Daytona" with "adapt the official reference," which is faster and more judge-credible.

## Net: recommended build stack for Fork Wars
Lane engine = **VibeKit** (agent-in-sandbox + streaming + PR automation, backend=Daytona) → models = **Fireworks** (GLM-5.2/Kimi K2.7/MiniMax M3/DeepSeek via AI SDK) → parallel pattern per **SWE-ReX**/best-of-N → UI = **CopilotKit Built-in Agent** (lanes as iframes + generative UI) → referee = **Braintrust** → gate = **CodeRabbit** on the VibeKit-created PR. Every sponsor load-bearing, every piece has an official reference implementation.
