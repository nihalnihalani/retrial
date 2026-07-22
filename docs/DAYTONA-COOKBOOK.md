# Daytona Cookbook — verified patterns for Fork Wars
Sources: live spike (SPIKE-RESULTS.md), Context7 `/websites/daytona_io` (471 snippets, authoritative), Daytona docs/OpenAPI, and the A/B GPT HackSprint-winner writeup.

## ✅✅ FORK VERIFIED WORKING (2026-07-23, us-east-1, real numbers)
Daytona granted a dedicated region **`us-east-1`** with linux-vm support. Fork works end-to-end. VERIFIED measurements:
- VM snapshot create: **~15s**; VM sandbox from snapshot: **~15s** (do both at 10:00, once).
- **Single fork ready: ~4–10s** (first fork ~26s cold, then 15s→10s→10s as the runner warms). Fork API *accepts* in ~2s.
- **State preservation: PERFECT** — all 4 forks inherited the seeded `buggy.py`, `marker.txt`, AND the `apt-get install python3` done in the base. Each ran `add(2,3) → -1` (the bug). This is the real win: install tooling ONCE in the base, all forks inherit it.

### ⚠️ Behavioral facts that change the plan (verified, not assumed)
1. **Forks CANNOT be created concurrently from the same base** → parallel fork calls return **409 Conflict** (only 1 of 4 succeeds). Forks must be **SEQUENTIAL** (retry-on-409). 4 sequential forks = **~60s total**. → PITCH FIX: do NOT say "one sandbox becomes four instantly." Either (a) narrate "we fork four lanes in about a minute" honestly, (b) **pre-fork the 4 lanes before the demo** and show them already racing (recommended — the fork isn't the wow, the RACE is), or (c) use the container snapshot fan-out (2s, concurrent) if you want the live "1→4" beat and don't need true VM memory-fork.
2. **Region MUST be `us-east-1`** (their granted region), NOT `us`/`eu`. `DaytonaConfig(target="us-east-1")`.
3. **Must create a NEW snapshot in-region** with `CreateSnapshotParams(name=..., image="ubuntu:22.04", sandbox_class=SandboxClass.LINUX_VM)`. Existing `daytona-vm-*` snapshots do NOT work there.
4. **Declarative builder does NOT work in us-east-1** (per Daytona) → bake tooling by running `apt-get install` in the base sandbox at runtime, THEN fork (forks inherit it). Verified: python3 installs in ~7s.
5. **Base `ubuntu:22.04` image is bare** — no python3. Install what each lane needs (python3, pip, pytest, git, node…) in the base before forking.
6. **Deleting the base fails while forks are alive** ("cannot delete sandbox which has active children") — delete forks first, then base.
7. Home dir on the VM = **/root** (not /home/daytona like containers) — `get_user_root_dir()` returns it; write files there.

### us-east-1 feature status (2026-07-23, RE-VERIFIED clean)
- **Preview URLs: WORK ✓✓** — `get_preview_link(8080)` returns in ~1.8s, fetched served content successfully (url form: `https://8080-{id}.daytonaproxy01.net`, needs `x-daytona-preview-token` header). The CopilotKit iframe race-grid is UNBLOCKED.
- **Egress: WORKS** — earlier ~9-min apt hangs were TRANSIENT (bad runner instance); clean re-test: `apt-get update` 9s, `install python3` 4s. Not a platform limit (Dalin confirmed egress isn't restricted). If a runner ever hangs, delete+recreate the sandbox.
- **Base `ubuntu:22.04` is bare** — no python3 AND no curl (curl → exit 127). Install everything you need (python3, curl, pip, git, node…) in the base before forking. Better: ask Daytona for a pre-baked tooling snapshot (allowed; just can't use declarative builder in-region).
- **`network_block_all=True` killed the SDK control channel on a VM** — do NOT demo firewall block-all on a VM lane; use `domain_allow_list` at create time if network scoping is needed, and test first.
- Concurrency/disk: base + 4 forks (5 VMs) coexist fine; Dalin confirmed ~6-8 alive is OK.

### Verified working fork loop (copy-paste)
```python
from daytona import Daytona, DaytonaConfig, CreateSnapshotParams, CreateSandboxFromSnapshotParams, SandboxClass
from daytona_api_client.api.sandbox_api import SandboxApi
from daytona_api_client.models.fork_sandbox import ForkSandbox
import time
c = Daytona(DaytonaConfig(target="us-east-1"))
api = next(getattr(c,a) for a in dir(c) if isinstance(getattr(c,a,None), SandboxApi))
snap = f"fw-{int(time.time())}"
c.snapshot.create(CreateSnapshotParams(name=snap, image="ubuntu:22.04", sandbox_class=SandboxClass.LINUX_VM), timeout=600)
base = c.create(CreateSandboxFromSnapshotParams(snapshot=snap), timeout=420)
base.process.exec("apt-get update -qq && apt-get install -y -qq python3")     # bake tooling ONCE
base.process.exec("echo 'def add(a,b): return a-b' > buggy.py")               # seed repo
# fork SEQUENTIALLY, retry on 409:
forks=[]
for i in range(4):
    for _ in range(20):
        try:
            r = api.fork_sandbox(base.id, ForkSandbox(name=f"lane-{i}-{int(time.time()*1000)%99999}"))
            while not str(c.get(r.id).state).lower().endswith("started"): time.sleep(1)
            forks.append(r.id); break
        except Exception as e:
            if "409" in str(getattr(e,'body',e)): time.sleep(1.5); continue
            raise
# each fork inherits python3 + buggy.py; now each lane's Fireworks model patches buggy.py and runs it
```

## 🎯 BIGGEST STRATEGIC FINDING
**The actual HackSprint #1 winner (A/B GPT) did NOT use fork.** Per Daytona's own writeup, for each variant it **created a fresh sandbox from the original repo**, spawned a Claude Code agent inside, and exposed a preview URL — then compared versions. That is *exactly our "Plan B" snapshot/repo fan-out*. So Plan B is not a fallback — **it is the proven winning pattern on this exact stage.** Fork is a nice-to-have optimization (shared warm state), not a requirement. This de-risks the whole plan: build on create-from-repo/snapshot fan-out (verified working on the free tier), treat true VM fork as an upgrade if Daytona enables it.

## The winning project's loop (A/B GPT, built in 5h, won 1st + Best Use of Browser Use)
1. Create a sandbox from the repo. 2. Spawn an agent (Claude Code) inside. 3. Agent applies changes; preview URL updates live. 4. Spin up N variants, each isolated. 5. Validate each (they used Browser Use; we use Braintrust test-pass + CodeRabbit gate). 6. Keep the best, loop.
→ Our Fork Wars = same loop, but 4 variants race in parallel with 4 different models, objective referee instead of ad-hoc, and a review gate. Coherent evolution of a proven winner.

## Verified core calls (Python SDK, `pip install daytona`)
```python
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams
client = Daytona(DaytonaConfig(target="us"))          # region matters; default snapshot = container (forkable=NO, but fine for Plan B)

# CREATE (0.71s measured)
sb = client.create(CreateSandboxFromSnapshotParams(
    labels={"purpose":"forkwars"},
    # secrets=[{"FIREWORKS_API_KEY": "fw-vault-secret-name"}],   # inject key via vault, not baked in
    # network_block_all=True / domain_allow_list="api.fireworks.ai",  # lock at create time (firewall is ~6s to apply if done later)
))

# EXEC — WRITE TO HOME DIR, never / (runs as non-root user `daytona`)
root = sb.get_user_root_dir()                          # = /home/daytona
sb.process.exec("echo 'def add(a,b): return a-b' > buggy.py")
out = sb.process.exec("python3 -c 'import buggy; print(buggy.add(2,3))'")  # -> '-1' (the bug), exit 0

# PREVIEW URL (1.6s round-trip) — private sandboxes need the token header
pv = sb.get_preview_link(8080)                         # -> {url, token}
# fetch with header  x-daytona-preview-token: pv.token

# CLEANUP
client.delete(client.get(sb.id))
```

## Concurrent fan-out (Plan B — the money shot, VERIFIED 2.06s for 4)
```python
import threading
def spawn(i, out):
    sb = client.create(CreateSandboxFromSnapshotParams(labels={"lane":str(i)}), timeout=120)
    sb.process.exec("...seed the repo + buggy file in /home/daytona...")
    out[i] = sb
lanes = {}
ts = [threading.Thread(target=spawn, args=(i, lanes)) for i in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]     # ~2s wallclock, all 4 live
```

## Snapshot-from-live-sandbox (for identical seeded state across lanes)
`POST /sandboxes/{id}/snapshots` — body `{name, includeMemory}`. Container sandboxes: no memory snapshot (state via filesystem only). VM + includeMemory=true needs STARTED. SDK: `client.snapshot.create(...)`. Pattern: seed a base once → snapshot → 4 lanes create from that snapshot with identical deps pre-installed → faster than re-seeding each.

## True VM fork (the upgrade, currently BLOCKED on free tier — see email)
- Endpoint: `POST /sandbox/{id}/fork` with `ForkSandbox(name=...)`. **Low-level client only** (`SandboxApi.fork_sandbox`) — high-level SDK has no fork; `sandbox.copy()` is a Pydantic decoy.
- Requires: VM-class snapshot (`daytona-vm-*`, currently regionless on this account), source sandbox STARTED (never pause first), disk headroom (free-tier 30GiB cap blocks it).
- Blocked until Daytona enables linux-vm regions + tier bump (email drafted in daytona-email.md).

## Fresh-feature knobs worth showing judges (all in the create call)
- `secrets=[{ENV_NAME: vault_secret_name}]` — inject Fireworks key via vault, "no keys baked in forks" (2 Daytona judges love this)
- `network_block_all` / `domain_allow_list="api.fireworks.ai"` — lock each lane to ONLY the model API ("safe agent")
- `linked_sandbox` — link a new sandbox to an existing one
- `computer_use` surface present (mouse/keyboard/screenshot/recording) — roadmap mention
- GPU: `daytona-gpu` snapshot available in `us` (vLLM/CUDA pre-baked) — roadmap mention

## How Fireworks plugs in (the brain vs hands split)
Daytona = the HANDS (sandbox, apply patch, run tests, PR). Fireworks = the BRAIN (picks the fix). They're separate layers; a lane differs from its neighbor by ONE string: the model ID.

**Fireworks is OpenAI-compatible** — base_url `https://api.fireworks.ai/inference/v1`, key `FIREWORKS_API_KEY`, model IDs `accounts/fireworks/models/{name}` (e.g. `glm-5.2`, `kimi-k2.7`, `deepseek-v4`, `minimax-m3`). Either the OpenAI SDK with that base_url, or Vercel AI SDK `@ai-sdk/fireworks` (`fireworks("accounts/fireworks/models/glm-5.2")`).

**The lane loop (identical per lane, only MODEL_ID changes):**
```python
# 1. BRAIN: ask a Fireworks model for the fix (runs on YOUR machine, not in the sandbox)
from openai import OpenAI
fw = OpenAI(base_url="https://api.fireworks.ai/inference/v1", api_key=os.environ["FIREWORKS_API_KEY"])
patch = fw.chat.completions.create(
    model=MODEL_ID,                      # <- the ONLY thing that differs across the 4 lanes
    messages=[{"role":"user","content": f"Here is a failing repo:\n{buggy_code}\nFailing test:\n{test}\nReturn the corrected file only."}],
).choices[0].message.content
# 2. HANDS: apply + test inside the Daytona lane sandbox
lane.process.exec(f"cat > buggy.py <<'EOF'\n{patch}\nEOF")
result = lane.process.exec("python3 -m pytest -q 2>&1; echo EXIT:$?")   # pass/fail + timing = the referee signal
```
So the "4-model race" is 4 copies of this loop, one MODEL_ID each, run concurrently — Braintrust scores the `result`, winner's patch becomes a PR, CodeRabbit gates it.

**DO NOT use VibeKit's bundled agents to drive Fireworks** — Claude Code / Codex / Gemini CLI are provider-locked to Anthropic/OpenAI/Google. Options, in order of demo-reliability:
1. **Custom minimal loop (above)** — RECOMMENDED. One Fireworks chat completion per lane, no agent framework, lowest failure surface, most legible ("same prompt, same bug, 4 models"). Best for a controlled seeded bug in 5.5h.
2. **OpenCode as the lane agent** — it's provider-agnostic (75+ providers incl. Fireworks via OpenAI-compat), so you get a real multi-step agent per lane. More capable, more failure surface.
3. **VibeKit for sandbox+PR plumbing only** — use its Daytona sandbox mgmt + GitHub PR automation, but supply your own Fireworks call for the brain (don't use its built-in CLIs). Good if the PR-automation saves time.
Fastest-tier note: append Fireworks Serverless 2.0 "Fast" tier for latency-critical lanes ("we chose Fast tier" is a narratable decision).

## Q&A ammo (verified facts)
- "Daytona went closed-source June 2026 citing AI-assisted vuln discovery in open repos" — reframe as WHY sandboxing AI code matters (their own argument).
- No native batch-fork API (GH#4001 open) — we fire concurrent single create/fork calls; ~2s for 4.
- Daytona sub-90ms sandbox creation claim: our measured container create was ~0.7s end-to-end incl. SDK round-trip; fast enough for a live "four in ~2 seconds" beat.
