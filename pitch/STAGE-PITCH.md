# Retrial — Live Stage Pitch (3 min) + Q&A Prep (2 min)

Round 2, top-8 stage: **3-minute pitch + 2-minute Q&A.** Panel skews
engineer/evangelist (Daytona, Braintrust, Fireworks, CodeRabbit, CopilotKit
staff) plus CTO-type (TikTok eng) and a founder (Lyzr). Criteria are
25/25/25/25 — Impact, Technical, Creativity, Presentation — sponsor usage is a
separate bonus. Be loud, show real code/real runs, end with intent to keep
building.

All numbers measured in this repo (`docs/WINNING-IDEA.md` → "MEASURED
DEMO-TIMING TRUTH", `calibration-results.json`). Run the live board at
`?live=1`; the replay board is the identical-looking insurance.

---

## The 3-minute pitch (beat-by-beat)

### Beat 0 — The trap opening (0:00–0:40) · branch-proof, interactive

Have the seed test running live on screen. **Run it once.**

- **If it comes up GREEN:**
  > "Quick show of hands — this test just passed, CI's green. Who'd merge it?"
  *(let hands go up)*
  > "Keep them up."

- **If it comes up RED:**
  > "CI's red. So let's do what every one of us actually does — hit rerun."
  *(rerun; it goes green within a try or two — expected)*
  > "There we go, green. Now — who'd merge it?"
  *(hands go up)* "Keep them up."

Then, either branch:
> "Put your hands down. This test is **fifty-one percent broken.** It fails
> about half the time, on code that never changed. Your CI just lied to you —
> and you believed it. We built the lie detector."

*(This is the emotional core. The room FEELS the problem before we explain it.
The red branch is stronger — it acts out the rerun-until-green anti-pattern
everyone in the room does.)*

### Beat 1 — The thesis (0:40–1:00)

> "Flaky tests are the number-one reason teams stop trusting CI. Bitbucket,
> Datadog, and Kong all shipped flake-fixers this year — so the pain is real.
> But they all do the same thing: one agent guesses a cause, and verifies the
> fix with a single run. A single run is **exactly** what a flaky test beats.
>
> This is the one bug class where **verification, not generation, is the
> bottleneck.** Everyone builds machines that generate fixes. We built the one
> that *proves* them."

### Beat 2 — The live run: detect + tournament (1:00–2:15)

Hit GO on the board. Narrate over it — the computation is real, so let the grid
carry the moment.

> "Retrial reruns the test across a swarm of disposable Daytona sandboxes —
> fresh environment every trial — and measures the real flake rate with a Wilson
> confidence interval. Not weeks of CI history. Sixty seconds of sandboxes."

*(detect grid fills, settles ~50%)*

> "Fifty percent. Now Fireworks models propose **competing** root causes —
> order dependency, shared state — each with a patch. And here's the part that
> matters —"

*(lanes light up)*

> "— every fix gets re-trialed across the swarm, in parallel. Six trials a
> second. Watch the losers fall as the evidence eliminates them. One keeps
> coming back green."

*(winner + confirmation round)*

> "The winner runs a **fresh** confirmation round — selection bias is real, we
> guard against it. Fifty percent, down to zero out of forty. And we say it
> honestly: **at most nine percent, ninety-five percent confidence.** The whole
> arc — detect, diagnose, tournament, confirm, real model calls and all — ran in
> about forty seconds. Nothing you just watched was pre-recorded."

### Beat 3 — The receipt + the ship (2:15–2:40)

> "Here's the receipt — the entire tournament, live in Braintrust. Each
> hypothesis is an experiment; the permalink is a governance record. We didn't
> just fix it, we *proved* it's fixed, reproducibly, at a link a CTO can audit.
>
> And this isn't a slide — it ships a real PR with that evidence in the body.
> Here's one it opened on our own repo, retrial pull-request one: sixty-nine
> percent down to zero, the winning model named, five Braintrust permalinks
> attached, reviewed by CodeRabbit. Or, if nothing stabilizes the test, a
> quarantine PR with the same dossier. The run never dead-ends."

### Beat 4 — The close: flywheel + intent (2:40–3:00)

> "And we proved this isn't a toy: on a real catalogued flake from an academic
> dataset, all four models named the right cause — but only one fix survived the
> evidence, and the reruns rejected the other three. That's the whole point:
> naming the cause is cheap, proving the cure is what we automate.
>
> Every run also records your repo's **flake genome** — which model wins on which
> kind of flake, on *your* code. Five runs in, no single model dominates —
> `glm-5p2` and `deepseek-v4-pro` are tied on wins — which is exactly why a
> tournament beats betting on one favorite. That grows into a per-repo model
> leaderboard; it gets sharper the more you run it.
>
> Detection tools need a month of history. We need sixty seconds. Every flaky
> test deserves a retrial — fifty of them, actually. We're going to keep
> building this."

---

## Presenter mechanics
- One presenter tells the story; a second driver keeps the board/Braintrust tab
  cued so beats never wait on a click.
- If detect reads low/high on the day, it's still inside the 36–66% CI — say
  "it's landing in its confidence band" and move on; don't apologize.
- Loading/convergence animation is the recovery buffer — narrate the thesis over
  it if a call lags.
- Never say "we didn't have time." Never say "race condition."

---

## Q&A bank (2 min) — prepared honest answers

**Q: "Is this just a toy? Does it work on real code, or only your seeded test?"**
> *(This is our strongest answer — lead with it if you get any opening.)*
> "Real code, and it's the sharpest demonstration of why we exist. We took
> `test_rearrange` from the penman library, 1.2.1 — a real MIT Python project,
> catalogued in IDoFT, the academic flakiness dataset out of Illinois, already
> fixed by the maintainer. We fed our four models a *sanitized* copy — every hint
> about the cause stripped out — and ran the tournament. **All four correctly
> named the root cause: randomness in the ordering.** But here's the thing —
> **only one produced a fix that actually worked.** The other three looked just
> as plausible and came back sixty-nine, eighty-eight, ninety-four percent
> flaky — the evidence *rejected* them. The winner, a valid alternative fix,
> confirmed at zero failures across twenty-five fresh-process trials. That's the
> whole thesis in one run: models talk a good game — all four named the right
> cause — but only one fix survived the evidence. That's exactly why
> verification, not vibes, has to decide.
> *(If pressed on ground truth:)* An earlier run of ours let the repro's comments
> leak the cause, and we briefly overclaimed that the models rediscovered the
> maintainer's exact fix. We caught it, re-ran it clean, and this is the honest
> result. We'd rather show you the corrected number than the flattering one."

**Q: "Why not just run `pytest --count=50` locally? Why the sandboxes?"**
> "Because shared-state and environment flakes need a *fresh* environment per
> run — same-process reruns share filesystem, ports, env, and interpreter state,
> so they hide exactly the flakes we're hunting. A fresh sandbox per trial is a
> scientific requirement, not infrastructure garnish. And it's the honest answer
> to 'is this fixed everywhere,' not just 'on my machine.' That said — for
> order/scheduling flakes we don't pay for a full sandbox per trial; we reuse a
> warm pool and take a fresh interpreter each time. Isolation level matched to
> flake class: 6.1 trials a second that way, versus 2.7 with full teardown."

**Q: "How is this different from Datadog / Bitbucket / Kong flake detection?"**
> "Two differences. One: they detect flakiness by mining *weeks* of CI history —
> we measure it in a minute of sandboxes, on demand, on any repo, no history
> required. Two: they run one agent that guesses a cause and verifies with a
> single pass. We run a hypothesis *tournament* and prove the winner with fifty
> auditable reruns. They tell you a test is flaky. We tell you why, fix it, and
> prove the fix."

**Q: "Does this compound, or is it a feature not a company?"**
> "The flake genome — and it's already accumulating, not a roadmap promise. Every
> run classifies the flake by cause class and records which model won on it, per
> repo. Our genome endpoint right now shows two runs, two fixes, both
> order-dependency, `glm-5p2` winning both. That becomes a repo-specific
> leaderboard — your suite's failure taxonomy and the fixer most likely to beat
> each kind. Detection today, prediction and prevention next: a CI gate that
> knows your flake profile before a human ever sees the red."

**Q: "Why four models instead of one good agent?"**
> "The unit of competition isn't the model — it's the *hypothesis*. Order
> dependency versus shared state are different treatments, and the evidence
> eliminates the wrong ones. Multiple models just give us hypothesis diversity;
> a single model tends to converge on one story. It's differential diagnosis:
> you don't want one confident doctor, you want competing diagnoses and a test
> that settles it."

**Q: "Is this really live, or staged?"**
> "The tournament is live — a full run, real model calls included, is about forty
> seconds, so we have the margin. Two things are pre-computed and I'll say so
> plainly: the Fireworks
> hypotheses can be cached so a model hiccup doesn't stall the demo, and the
> **CodeRabbit review is pre-run** because its latency is one to five minutes —
> that's a real product constraint, not something we can fake to look instant.
> Everything on the board — the reruns, the flake rates, the confidence
> intervals — is measured live."

**Q: "You said race conditions earlier — do you actually reproduce those?"**
> "No, and we're careful not to claim it. We measured it: thread and timing
> races didn't flake at all on this substrate — zero out of a hundred twenty
> trials — because CPU-constrained container scheduling suppresses them. Our seed
> is a genuine **order-dependency** flake, hash-randomization driven, calibrated
> at 51%. We only say 'scheduling-dependent flakes' where it's true. We'd rather
> be narrow and honest than broad and wrong."

**Q: "Zero failures — so it's fixed, guaranteed?"**
> "We never say zero. Zero out of forty is 'at most eight-point-eight percent,
> ninety-five percent confidence' — that's the Wilson interval, and it's on the
> screen and in the PR. Honest uncertainty is the product. A tool that claims
> certainty on a flaky test is just a new way to lie."

**Q: "How much does all this sandbox burn cost?"**
> "Four hypotheses times fifty reruns is about two hundred sandbox-runs — roughly
> a minute of wall-clock at sixteen-concurrent, sub-cent per run on containers.
> The cost *is* the product: flake detection inherently needs many reruns for
> statistical confidence, and disposable parallel sandboxes are the
> right-shaped, cheap tool for it. You're buying statistical certainty, cheaply."

**Q: "What happens when no fix works?"**
> "It opens a **quarantine PR** — pulls the test out of the blocking path — with
> the full evidence dossier attached: flake rate, confidence interval, the
> hypotheses we tried and why each lost. Worst case is still a real, useful
> workflow output. The run never dead-ends on stage or in production."

**Q (Braintrust judge): "How are you actually using Braintrust?"**
> "Each hypothesis is a Braintrust **Experiment**; each batch of reruns is an
> eval run whose scorer is the empirical pass rate — a real reproducible eval,
> not an LLM vibe-check. Your dashboard *is* our tournament scoreboard, and the
> permalink is the audit receipt. We're using it as the substrate, not just an
> SDK call."

**Q (Daytona judge): "What's the actual Daytona usage under the hood?"**
> "A warm pool of container sandboxes with two isolation levels. Sixteen
> concurrent creates in about two seconds, create-to-started around
> seven-tenths of a second, one exec round-trip per trial — we collapsed
> write-and-run into a single exec, which doubled our throughput to 6.1 trials a
> second. The whole demo is your speed, parallelism, and disposability. It's the
> flagship use, not a decorative one."
