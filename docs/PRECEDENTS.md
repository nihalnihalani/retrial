# Precedent Research — past Daytona HackSprint winners

## ⚠️ DEDUPE FLAG for champion (Fork Wars)
**Code Quintet won HackSprint #2 (Nov 2025)**: multiple solution variants benchmarked in Daytona sandboxes, human picks the best — structurally close to Fork Wars' core mechanic, and Daytona's team (who judge repeatedly) saw it win. Differentiators to lead with UNPROMPTED in the pitch: multi-MODEL race (not multi-sample from one model), Braintrust eval-as-judge (not human preference), CodeRabbit PR gate, CopilotKit live UI, 5-sponsor integration (all past winners used 1-2 sponsors).

## Recurring winning genre across HackSprints
"Agent finds problem → fixes → re-verifies in a Daytona sandbox" (A/B GPT, QoalA, Aksu, ChaosAgent, PatchPilot, Reflexiv-PR). RECOMMENDATION: add a self-heal beat to Fork Wars — losing forks get fixed from eval feedback — rides the strongest precedent without duplicating any single past project.

## Past winners
- #1 SF Oct 2025: A/B GPT (UX-issue finder/fixer, Daytona+Browser Use); PolySandbox; QoalA.
- #2 SF Nov 2025: ChaosAgent (LLM stress-testing); Aksu (a11y auto-fix); Code Quintet.
- NYC Dec 2025: 10xhr.ai; PitchBox (auto-runs repo in Daytona); LeetCort (voice courtroom).
- #4 SF Jan 2026: Codevolution, Paradigm MCP, Reflexiv-PR, RabbitReview (CodeRabbit+Daytona PR vuln detection — prior art for CodeRabbit+Daytona combos), Voice Arena.

## API verification
Daytona fork/snapshot endpoints CONFIRMED in docs (copy-on-write, fork trees, forks-of-forks). Caveat: open GH issue #4001 "Parallel Sandbox Execution API" suggests dedicated parallel-fork tooling still maturing — hands-on check at 10:00 mandatory.

## Judge intel (thin public record)
- Jerel Velarde (CopilotKit CTO): has judged before; praised "creativity, execution speed, ambition"; CopilotKit criteria: working prototypes, code quality, architecture, observability. VERIFIED.
- All other judges: no public judging record — don't assume preferences. UNVERIFIED.
