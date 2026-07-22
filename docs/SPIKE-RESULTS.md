# Daytona Claim Verification — SPIKE RESULTS (2026-07-20, live account testing)

Every claim tested against the real API with this account (free tier, org region us). Sandboxes created and deleted; ~zero residual cost.

## ✅ VERIFIED WORKING
| Claim | Result |
|---|---|
| SDK package naming | `pip install daytona` ✔ (0.199.0; `daytona-sdk` mirrors same versions — either works) |
| Container sandbox create speed | **0.71s create→started** (measured) — blazing |
| Preview URLs | ✔ end-to-end: served HTTP from sandbox port 8080, fetched via public URL in **1.6s**. Gotcha: needs `x-daytona-preview-token` header (from `get_preview_link()`) for private sandboxes |
| Domain Firewall (block-all) | ✔ works via `sandbox.update_network_settings(network_block_all=True)` — but takes **~5.7s to take effect** (NOT instant). Safe pattern: set `network_block_all`/`domain_allow_list` at CREATE time (params support it) |
| Secrets Manager | ✔ full CRUD at `client.secret` (create/delete/get/list/update) + `sandbox.update_secrets()`. Gotchas from docs: attached secrets take effect for outbound in seconds, but new env vars only visible to NEW processes; secretless sandboxes need restart |
| Windows VM create | 14–16s to started (fast); `process.exec` works with `cmd /c` |
| Fork API exists | ✔ real endpoint: `SandboxApi.fork_sandbox(sandbox_id, ForkSandbox(name=...))` — **NOT in the high-level SDK** (`Sandbox` class has no fork; `sandbox.copy()` is a Pydantic false-friend). Must use the low-level `daytona_api_client` |

## 🚨 BLOCKERS FOUND (the spike's whole purpose)
1. **Fork is VM-class only.** Container sandboxes → 422 "Forking is not supported for this sandbox". Default snapshot = container. Docs confirm: "Forking is supported for VM sandboxes only."
2. **All `linux-vm` snapshots have `regionIds: []`** on this account — deployed to ZERO regions. Linux VM sandboxes cannot be created at all right now (`daytona-vm-small/medium/large/ubuntu-xxl` all fail in us AND eu).
3. **Fork requires a STARTED sandbox** — "Sandbox must be in started state to fork". Pausing first is an error; the "Pause & Fork" branding is misleading for the API flow.
4. **Free-tier 30GiB disk cap blocks Windows fork**: windows-small (15GB) + fork (15GB) exceeds it even on a clean account. Error says: upgrade tier at app.daytona.io/dashboard/limits.

**NET: the Fork Wars money shot (true VM fork) is UNVERIFIABLE and currently UNRUNNABLE on this account tier. Fork latency and state preservation remain unmeasured.**

## ✅ PLAN B VERIFIED END-TO-END (the fallback works)
- **4 concurrent container creates: 2.06s total wallclock** (individual: 0.97–2.06s). This is the money-shot fallback — "one environment becomes four in ~2 seconds" is a TRUE statement on the free tier today.
- **exec write/read/run works** — but ONLY in the user home dir. Sandbox runs as non-root user `daytona`; writing to `/work` or any root path fails with Permission denied. Use `sandbox.get_user_root_dir()` (= `/home/daytona`) as the working dir. Ran the seeded buggy function live: `add(2,3)` returned `-1` (the bug), exit 0. This is the exact demo operation and it works.
- **computer-use: full surface present** — `mouse, keyboard, screenshot, display, recording, accessibility, start/stop, get_status, process logs`. Not tested live (needs a GUI target) but the API exists.
- **GPU snapshot `daytona-gpu`: available in `us`, class=container** (Dockerfile pre-bakes vLLM 0.21 + CUDA 13 + FlashInfer). So GPU sandboxes ARE creatable here — but heavy; not needed for the chosen plan.
- **Secrets: `CreateSecretParams` = name, value, description, hosts** — `hosts` field scopes a secret to specific domains (nice audit story).

## EVENT-DAY PLAN CHANGES (mandatory)
1. **First booth question at 9:00 (before any building):** "We're building our demo on VM fork — can you enable linux-vm snapshot region access + a tier/disk bump for hackathon accounts?" Daytona HOSTS the event; this is exactly what booth engineers are for. The $100 attendee credits may auto-bump the tier — verify immediately.
2. **10:00 spike GO/NO-GO (15 min hard limit):** if linux-vm works → measure real fork latency, use the measured number in the pitch. If not →
3. **PLAN B (verified today, works on free tier): snapshot fan-out.** Snapshot the seeded base sandbox → concurrently create 4 container sandboxes from it (0.71s each, measured). On stage it looks identical ("one environment becomes four"), narrated honestly as "we fan out from a live snapshot — four isolated copies in about a second." All other beats (race, referee, gate, preview-URL vote page, firewall lockdown at create time) work UNCHANGED on containers.
4. **Never say "VM Pause & Fork" in the demo script** unless true fork is confirmed working that morning; the API flow doesn't even use pause.
5. Pitch language: replace the unverified "under a second" fork claim with whatever number is measured on the day (fork) or the verified ~0.7s (snapshot fan-out).

## Corrections to VERDICT.md assumptions
- "4 concurrent `fork()` calls, each returns sub-second" — UNVERIFIED, and currently impossible on this tier. Use Plan B language until proven.
- Domain-Locked upgrade: firewall works but propagation is ~6s when applied to a running sandbox → apply at fork/create time (param exists), not as a live retrofit.
- The Q&A answer about GH#4001 stands (no batch fork; concurrent single calls).
