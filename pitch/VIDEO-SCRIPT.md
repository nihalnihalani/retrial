# Retrial — Devpost Video Script (< 2 min)

Target length **1:45–1:55** (hard ceiling 2:00). Screen recording, voiceover.
Cold open must land the hook in the first 15 seconds — no scene-setting, no logo
card first. All numbers are measured (see `docs/WINNING-IDEA.md` → "MEASURED
DEMO-TIMING TRUTH", `calibration-results.json`). The calibrated seed is 51%
flake (CI 36–66%); a single live run re-measures within that interval.

**Recording setup (do this before you hit record):**
- Start the engine server (`uvicorn retrial.server:app --port 8000`) with the
  pool pre-warmed, and open the board at **`http://localhost:5173/?live=1`** so
  the run streams over WebSocket from a real tournament.
- Run with **`MAX_TRIALS=40`+** so the confidence intervals visibly tighten —
  weak CIs read as weak evidence on camera.
- Have the split-screen "caught in the act" view ready as the very first frame.
- Fallback if live is flaky on the day: the default replay board
  (`http://localhost:5173/`) is the winning-path scripted run — visually
  identical, spotless console. Record off `?live=1` if it's stable; the replay
  is insurance, not the plan.

---

## Shot list at a glance

| # | Time | Screen | VO beat |
|---|------|--------|---------|
| 1 | 0:00–0:15 | Split-screen: same test, pass left / fail right | The trap — "it passed 3 times today; it's 51% broken" |
| 2 | 0:15–0:30 | Board: run_started, detect grid filling green/red | Detect = the lie detector, Wilson CI |
| 3 | 0:30–0:45 | Diagnose: hypothesis cards appear (Fireworks) | Competing root-cause hypotheses |
| 4 | 0:45–1:10 | Tournament: parallel lanes filling, eliminations | The retrial — 6.1 trials/s, lanes converge |
| 5 | 1:10–1:30 | Winner + confirmation round; Braintrust permalink | 50%→0/40, "≤8.8% at 95% confidence", the receipt |
| 6 | 1:30–1:45 | Genome card + PR / CodeRabbit; tagline card | Flywheel + close |

---

## Shot 1 — Cold open: the lie, caught in the act (0:00–0:15)

**SCREEN:** Open on the **split-screen "caught in the act"** view — the *same*
test, same code, running in two sandboxes at once. Left pane resolves **PASS
(green)**. Right pane resolves **FAIL (red)**. Hold both on screen together.
Overlay text stamps in: **"Same test. Same code. Same second."**

**VO (0:00):**
> "This test passed three times today. Your CI is green. You'd merge it."
> *(beat)*
> "It's fifty-one percent broken — and it just passed and failed at the same
> instant, on the same code."

**SCREEN (0:10):** Quick cut to a title card — **RETRIAL** — with the line
*"Your build isn't broken. It's lying."*

---

## Shot 2 — Detect: the lie detector (0:15–0:30)

**SCREEN:** The tournament board. `run_started` fires; the **detect grid**
starts filling — a wall of cells flickering green and red as trials land across
the Daytona swarm. A live counter and a **Wilson confidence interval** tick
alongside it, tightening as trials accumulate. It settles around **50% flake**.

**VO (0:15):**
> "Retrial reruns it across a swarm of disposable Daytona sandboxes — a fresh
> environment every trial — and measures how often it actually fails. Not from
> weeks of CI history. From sixty seconds of sandboxes."

**VO (0:24):**
> "Fifty percent, with a real confidence interval. That's the lie, made into a
> number."

---

## Shot 3 — Diagnose: differential diagnosis (0:30–0:45)

**SCREEN:** Detect resolves; the board transitions to **diagnose**. Two or three
**hypothesis cards** flip in — each labeled with a cause class (order
dependency, shared state) and a one-line explanation. Small "Fireworks" model
tags on each.

**VO (0:30):**
> "Then frontier models on Fireworks don't guess *one* cause — they propose
> competing ones. Order dependency. Shared state. Each with a patch to test."

**VO (0:39):**
> "We don't pick the smartest-sounding fix. We make them earn it."

---

## Shot 4 — The tournament (0:45–1:10)

**SCREEN:** The board splits into **parallel lanes**, one per hypothesis. Each
lane fills its own grid of green/red trials, fast. This is the money shot — let
it breathe. As a lane's confidence interval stops beating the original rate, it
**greys out / eliminates** with a soft strike. The surviving lane stays vividly
green.

**VO (0:45):**
> "Every fix gets re-trialed across the swarm — in parallel. Six-point-one
> trials a second."

**VO (0:54):**
> "Watch the losers fall. A fix that only *looks* right can't survive fifty
> reruns. The evidence eliminates it."

**VO (1:03):**
> "One hypothesis keeps coming back green."

---

## Shot 5 — Winner, confirmation, the receipt (1:10–1:30)

**SCREEN:** `winner_confirmed`. The winning lane runs a distinct **fresh
confirmation round** (call it out visually — a new grid, all green). Numbers
resolve: **50% → 0/40**, rendered as **"≤8.8% at 95% confidence."** Cut to the
**Braintrust experiment permalink** — the dashboard showing the before/after
rate across the swarm (the real shipped run carries 5 of these).

**VO (1:10):**
> "The winner runs a fresh confirmation round — because selection bias is real,
> and we guard against it."

**VO (1:18):**
> "Fifty percent down to zero out of forty. We report it honestly — at most
> nine percent, ninety-five percent confidence. And here's the receipt: the
> whole tournament, live in Braintrust. We didn't just fix it. We proved it."

---

## Shot 6 — Flywheel + close (1:30–1:45)

**SCREEN:** The **flake genome** card slides in — cause-class taxonomy, model
win-rates (real data: `glm-5p2` and `deepseek-v4-pro` tied on wins across 5 runs). Quick
glimpse of the **real opened PR — retrial#1** — with the evidence dossier +
Braintrust permalinks + a **CodeRabbit** review badge. Land on the tagline card.

**VO (1:30):**
> "It ships a real pull request — this one's live on our repo — with the
> evidence attached, reviewed by CodeRabbit. And every run teaches Retrial your
> repo's flake genome, so it gets sharper the more you run it."

**VO (1:40), on the tagline card:**
> "Every flaky test deserves a retrial. Fifty of them, actually."

**OPTIONAL — proof title card (≈5s, 1:45–1:50, only if under the 2:00 ceiling):**
Silent full-screen text over the board, no VO (or a single line if room):
> **On a real catalogued OSS flake, all four models named the cause — only one
> fix survived the evidence. The other three were rejected on measured reruns.**
> *penman v1.2.1 · IDoFT · winner confirmed 0 fails / 25 trials · 3 rejected at 69/88/94%*

**SCREEN (end card):** RETRIAL · *the lie detector for flaky tests* ·
`github.com/nihalnihalani/retrial`

---

## Honesty notes for the recording (non-negotiable)

- The **CodeRabbit** review shown is pre-run (its latency is 1–5 min) — don't
  cut it to imply live turnaround. If narration ever implies timing, say
  "reviewed by CodeRabbit," not "reviewed just now."
- Never show or say **race condition** — measured 0/120, those don't flake on
  this substrate. The seed is an **order-dependency** flake; say "order
  dependency" or "scheduling-dependent," never "race."
- The 51% is the calibrated lock; the ~50% on screen is that run's own detect
  reading inside the CI. Don't present the per-run reading as separately
  calibrated. The real shipped PR (retrial#1) shows 69%→0% — a different run's
  numbers; don't splice them into the on-screen arc.
- Prefer recording off a genuinely live `?live=1` run so the claim "nothing here
  is pre-recorded" stays true. If you fall back to replay for stability, don't
  make the "fully live" claim in the video's VO.
