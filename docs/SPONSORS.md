# Daytona HackSprint #5 — Sponsor Playbook

Event: https://luma.com/hacksprint-sf — SF, July 2026. Hacking 10:00–15:30, submit by 15:30, 3-min demo + slides, teams ≤4.
Judging: originality, technical excellence, real-world applicability. $35k+ pool with per-sponsor "Best Use of X" prizes — stack them.

Attendee credits: $100 Daytona, $250 Braintrust, 1mo ElevenLabs Creator, $50 Fireworks, WorkOS 12-mo custom domain, CodeRabbit 14-day trial, CopilotKit Enterprise token.

## MCP reality check
**Daytona, Braintrust, ElevenLabs, and WorkOS** publish official MCP *servers* (WorkOS has two: a docs server and a live dashboard-control server). CodeRabbit and CopilotKit are MCP *clients* — don't pitch "integrating their MCP server." Fireworks has a docs-only MCP plus a beta server-side MCP-calling feature in its Response API.

CONFIRMED: there is **no "Best Use of WorkOS" prize** — WorkOS integration is optional polish, not prize-bearing. Prize-bearing sponsors: Daytona ($1k cash), CodeRabbit ($1k cash), CopilotKit ($500 + Ray-Ban), Braintrust ($500), Fireworks ($500), ElevenLabs ($1,980/member Scale tier). Strategy: integrate 3–5 sponsors deeply and coherently; don't kitchen-sink all seven.

## Daytona — sandboxes ("Best Use" = $1k + $10k credits)
- CLI installed at `/opt/homebrew/bin/daytona`. Auth: `daytona login` (browser). MCP: `daytona mcp start` (already in `.mcp.json`).
- SDKs: Python `pip install daytona` (NOT the older `daytona-sdk`/`daytona_sdk`); npm `@daytonaio/sdk` — being renamed to `@daytona/sdk`, check docs.daytona.io for the current name before committing imports. Covers sandbox lifecycle, fs ops, git, LSP, process/code exec, computer use.
- **Judge bait:** MCP server's *computer-use* tool (agent-driven GUI control inside a sandbox) and *preview URLs* (agent builds an app in a sandbox and serves a live link in one flow).

## Braintrust — evals/tracing (Gold partner)
- SDK: `npm i braintrust` / `pip install braintrust`. Fastest path: `initLogger()` + `wrapOpenAI(client)` (TS) or `wrap_openai` (Python) — auto-traces every LLM call; Python also auto-instruments Anthropic/Gemini/Mistral/LangChain/LlamaIndex/etc. Use `autoevals` (github.com/braintrustdata/autoevals) for pre-built model-graded scorers — an `Eval()` in a few lines.
- MCP (in `.mcp.json`, needs `BRAINTRUST_API_KEY` in the Authorization header): `https://api.braintrust.dev/mcp` — query your own experiments/logs via SQL, summarize evals, permalinks, all in-editor.
- **Judge bait:** live "ask the coding agent why eval run #3 regressed" against your own traces — the MCP query layer is new and almost nobody uses it.

## ElevenLabs — voice
- MCP (in `.mcp.json`, needs `ELEVENLABS_API_KEY`): `uvx elevenlabs-mcp` — text_to_speech, speech_to_text, voice_clone, text_to_voice, isolate_audio, create_agent, ~24 tools. Free tier: 10k credits/mo.
- Skills installed: `text-to-speech`, `agents`, `setup-api-key`.
- Agents Platform (voice agents with WebRTC streaming + tool calling): dashboard, REST API, or Agents CLI; TS SDK at github.com/elevenlabs/packages.
- **Judge bait:** custom **client-side tools** — the voice agent calls functions running in your browser UI, not just backend; `text_to_voice` (design a brand-new voice from a text prompt, no reference audio); `isolate_audio` for one-call mic-noise cleanup.

## Fireworks AI — inference
- Vercel AI SDK: `@ai-sdk/fireworks`; or any OpenAI client with `base_url=https://api.fireworks.ai/inference/v1`.
- Docs MCP added to `.mcp.json` (`fireworks-docs`) — docs lookup only.
- Standout models (all recent adds): GLM 5.2 (day-zero, Jun 15), Kimi K2.7 Code, MiniMax M3, Qwen 3.7 Plus (Jun 12), DeepSeek, gpt-oss.
- **WARNING (Jun 10, 2026): image generation + audio inference DISCONTINUED from serverless** — do NOT plan a Fireworks FLUX/image demo.
- Serverless 2.0 (May 26): Standard / Priority / Fast tiers on one API — "we picked Fast tier for latency-critical agent calls" is a legit technical decision to narrate.
- **Judge bait (test EARLY, beta):** Response API server-side MCP tool-calling — model calls remote MCP servers itself: `tools=[{"type":"sse","server_url":"..."}]`. Or use a same-week model (GLM 5.2). Fallback: FireFunction with `tool_choice="any"` for routing.

## WorkOS — auth
- `npm i @workos-inc/authkit-nextjs`; `authkitMiddleware()` in middleware.ts (proxy.ts on Next 16). "Zero to authenticated in 5 minutes."
- TWO official MCP servers (both in `.mcp.json`): `@workos/mcp-docs-server` (docs/examples/changelog lookup) and the hosted **dashboard MCP** at `mcp.workos.com/mcp` (OAuth via WorkOS Connect) — agent can read/modify your live workspace: orgs, SSO connections, users, branding, with destructive-action confirmations.
- AuthKit can also **secure an MCP server you build** via `experimental_withMcpAuth` (Vercel MCP adapter). Reference: github.com/workos/mcp.shop.
- **Judge bait:** manage your WorkOS org by natural language through the dashboard MCP, or ship your project's own MCP server protected with AuthKit — ties auth + MCP together; nobody else will do this. (Caveat: no WorkOS prize line on Luma — confirm on-site.)

## CodeRabbit — code review
- Install: `curl -fsSL https://cli.coderabbit.ai/install.sh | sh`, then `cr auth login`. Free tier: 3 reviews/hour.
- Skills installed: `code-review`, `autofix`. MCP client, not server.
- **Judge bait:** `coderabbit review --agent` (structured JSON) wired into your agent loop — closed-loop "agent reviews its own PR, then fixes it."

## CopilotKit — agentic UI
- `npm i @copilotkit/react-core @copilotkit/react-ui @copilotkit/runtime`; skill installed (+ session plugin skill with full hook docs).
- **Judge bait:** MCP Apps rendering inside your app via `@ag-ui/mcp-apps-middleware` (`npx copilotkit create -f mcp-apps`) — brand-new in 2026; and `useCopilotAction` driving real app state (generative UI), not just a chat sidebar.

## Fresh releases — "shipped weeks before the event" judge bait
(Verified against official changelogs/blogs unless noted; newest first.)

**Top 3 picks:**
1. **WorkOS Management MCP Server** (Jul 1) — hundreds of dashboard ops via `mcp.workos.com/mcp`; killer demo: *paste a screenshot of a login-page design, agent configures a matching branded WorkOS login page in one prompt*. OAuth-scoped tokens, prod-access toggles.
2. **CodeRabbit `@coderabbitai fix-ci`** (Jul 16!) + **Security Agent** (Jul 9) — CI fails → CodeRabbit opens a stacked fix PR; Security Agent scans whole codebases (not just diffs) with CWE titles + exploit paths (Jul 10). Perfect closed-loop "agent fixes its own code" story.
3. **CopilotKit MCP Apps** via `@ag-ui/mcp-apps-middleware` (announced Jun 30) — render another server's MCP-defined UI inside your app; ~3 weeks old, almost no public examples.

**Also new:**
- **Daytona:** VM Pause & Fork (Jul 10 — branch a running sandbox into parallel experiments), Domain Firewall (Jul 9 — allow-list what an agent can reach = "safe agent" pitch), Secrets Manager (Jul 8), GPU sandboxes with vLLM/SGLang serving (Jul 6+), Windows sandboxes (Jul 7). **Official Daytona+CopilotKit integration guide exists (v0.186.0, Jun 10) and a Vercel AI SDK guide (v0.183.0)** — check before building from scratch.
- **ElevenLabs** (dates via aggregator; cross-check official changelog): nested agent transfers push/pop/replace (Jul 13 — multi-persona handoff stack), MCP environment scoping (Jul 13), tool interruption modes (Jul 6 — barge-in control during tool calls), Music v2 (Jun 22), per-agent turn-model selection (Jun 8), Speech Engine (May 25 — bolt real-time voice onto any existing text chatbot).
- **Braintrust** (July, exact days unlisted): LLM-judge "Skip" option (more honest evals), Topics digest (daily Slack summary of your agent's failure modes), GLM-5.2 access via Braintrust provider through Jul 31; Classifiers (categorical evals, Jun), **Gateway provider failover** (Jun — agent survives a provider outage).
- **WorkOS also:** Widgets API (Jul 3 — session-aware GraphQL for building UI from WorkOS data; pairs with CopilotKit), Step-up Auth (Jul 2 — re-verify before sensitive agent actions), AuthKit for Astro (Jul 7).
- **CopilotKit caveat:** the "Channels" v0.2 rebrand (Jul 15–17, Slack/Discord/WhatsApp bots) is a *separate product line* from the in-app AG-UI stack — don't conflate in the pitch. Enterprise Intelligence memory layer (May 14) may be enterprise-gated; the attendee "Enterprise Intelligence Token" perk suggests access — confirm at booth.
- **CodeRabbit also:** git-history secret scanning (Jul 15), agent-formatted security-finding sharing (Jul 11), CLI v0.6.0 fast review mode (Jun 9). Discord agent beta is star-gated (1k+ stars) — likely unusable for a fresh hackathon repo.

## Jul 21 re-sweep — corrections + brand-new finds
- **⚠️ Daytona fork DATE correction:** the changelog shows fork/snapshot endpoints shipped **Apr 14-15, 2026 (V0.165/166, "experimental")** — NOT July. The earlier "Pause & Fork Jul 10" item was likely the blog announcement of GA/branding. Pitch language: say "Daytona's fork capability" — do NOT claim "shipped last week" unless the booth confirms a July GA date. (Changelog page lags: latest entry Apr 27 V0.170, but the live API is v0.199.)
- **Braintrust NEW — Sandbox Evals (Beta):** push an eval once, run it on demand from the playground against custom agent code (AWS Lambda py/ts, Modal ts). Also: datasets pinned to environments (dev/staging/prod), Java auto-instrumentation. **Pitch gold: Braintrust themselves just shipped "evals that run in sandboxes" — our project runs sandboxes that feed evals. Natural resonance to name-check to their judge.**
- **ElevenLabs July adds:** Speech Engine now converts an existing chat agent to full voice with ONE prompt; **Procedures** (packaged SOP-style instruction sets for agents); **Flows Agent** (node-canvas workflow builder); agents accept images/files/audio/locations; Scribe v2 `keyterms` + `no_verbatim`. Announcer idea unaffected (uses TTS/text_to_voice).
- **Fireworks/Kimi K3 status:** K3 is API-live at Moonshot (Jul 16) but **NOT confirmed on Fireworks yet**; weights land Jul 27. Fireworks hosted K2.5/2.6/2.7 day-zero and is an "expected early host" — keep the day-of check.
- **CodeRabbit:** fix-ci (Jul 16) reconfirmed via changelog; also new "Change Stack" AI-native review interface + Issue Planner on the blog.
- **CopilotKit:** nothing newer than Jun 30 (AgentCore AG-UI); $27M Series A in May — team will be energized.

## Setup checklist (do at doors-open, 9:00–10:00)
- [ ] `daytona login`
- [ ] ElevenLabs API key → `.mcp.json` (`ELEVENLABS_API_KEY`)
- [ ] Braintrust API key → `.mcp.json` (Authorization header)
- [ ] Fireworks API key (env `FIREWORKS_API_KEY`)
- [ ] `cr auth login` (CodeRabbit)
- [ ] WorkOS dashboard: create project, grab client ID/API key
- [ ] Redeem attendee credits at each booth
