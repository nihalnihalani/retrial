"""SANITIZED RERUN — full Retrial tournament on the REAL penman flake, but with a
neutral repro that contains NO root-cause hints (goodmami/penman
tests/test_layout.py::test_rearrange, IDoFT NOD, fix = PR #102).

Why this exists: the ORIGINAL run (tournament_penman.py) fed the models a repro
whose header comments literally named the cause ("module-level random.seed
ineffective ... run unseeded"). That invalidates any "independently rediscovered"
claim. This rerun feeds `penman_repro_sanitized.py` instead — the same failing
test + its code, with every hint comment stripped and a neutral docstring — so we
learn what the models propose from the failing test ALONE.

Faithfulness to the engine: hypothesis generation reuses the engine's own building
blocks imported as-is (`_build_messages`, `_complete`, `_parse_hypothesis` from
`retrial.diagnosis`) — the prompt and parser are literally the engine's. This
script does NOT modify any engine/ file; it only wraps the engine so it can save
the prompt sent + raw model response + extracted patch as artifacts (which the
plain `diagnose()` discards).

Verify/confirm loops reuse the calibration-harness pattern: create sandboxes,
`pip install penman==1.2.1` once per sandbox, then rerun each candidate as fresh
python3 processes (each fresh process = fresh random entropy = the flake). Wilson
95% CI throughout.

Run:  .venv/bin/python seeds/real/tournament_penman_sanitized.py
"""
import os, sys, json, math, time, threading, subprocess
from pathlib import Path
from dotenv import load_dotenv

# Repo root derived from this file's location (seeds/real/<this>.py -> repo root).
RETRIAL = Path(__file__).resolve().parents[2]
load_dotenv(RETRIAL / ".env")
sys.path.insert(0, str(RETRIAL / "engine"))

from retrial.diagnosis import (          # engine building blocks, imported as-is
    _build_messages, _complete, _parse_hypothesis, CAUSE_CLASSES, _models_from_env, BASE_URL,
)
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

client = Daytona(DaytonaConfig(target=os.environ.get("DAYTONA_TARGET", "us")))

# --- inputs: the SANITIZED flaky repro (no cause hints) + a real failing run log.
# The failing log is inlined (not read from scratch) so this script is self-
# contained. It shows only GOT vs EXPECTED serialization — the same output a real
# CI failure would print — and names no root cause, so it leaks no hint either.
FLAKY_CODE = (RETRIAL / "seeds/real/penman_repro_sanitized.py").read_text()
LOG_TAIL = (
    "=== test_rearrange FAILED ===\n"
    "assert codec.format(t) == expected\n"
    "GOT:\n"
    "(a / alpha\n"
    "   :ARG0 (b / beta\n"
    "            :ARG0 (g / gamma)\n"
    "            :ARG1 (d / delta))\n"
    "   :ARG0-of d\n"
    "   :ARG1 (e / epsilon))\n\n"
    "EXPECTED:\n"
    "(a / alpha\n"
    "   :ARG0-of d\n"
    "   :ARG1 (e / epsilon)\n"
    "   :ARG0 (b / beta\n"
    "            :ARG0 (g / gamma)\n"
    "            :ARG1 (d / delta)))\n"
)
TEST_NAME = "tests/test_layout.py::test_rearrange"

# A safety-net driver appended to each candidate: it only fires if the candidate
# did NOT already sys.exit (sys.exit terminates first, so sys.exit-style patches
# are untouched). Handles pytest-style patches that merely DEFINE test funcs.
# HONESTY FIX: a candidate that neither sys.exit()s nor defines a runnable test
# function has produced NO executable check — it must FAIL the trial, not pass.
# (The old `else: exit(0)` silently crowned no-op / assertion-stripped candidates.)
DRIVER = """
# --- retrial harness driver (fires only if the patch did not sys.exit) ---
import sys as _s
_t = [v for k, v in list(globals().items())
      if k.startswith('test') and callable(v) and getattr(v, '__code__', None)
      and v.__code__.co_argcount == 0]
if _t:
    try:
        for _f in _t: _f()
        _s.exit(0)
    except SystemExit:
        raise
    except BaseException:
        import traceback; traceback.print_exc(); _s.exit(1)
else:
    print("RETRIAL_HARNESS: candidate produced no sys.exit() verdict and no "
          "zero-arg test function -- no executable check, failing trial",
          file=_s.stderr)
    _s.exit(1)
"""


def utc_now():
    """ISO-8601 UTC timestamp via `date -u`, shelled at call time (per task)."""
    return subprocess.check_output(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]).decode().strip()


def wilson(f, n, z=1.96):
    if n == 0: return (0.0, 0.0, 1.0)
    p = f / n; d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    m = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (p, max(0.0, c-m), min(1.0, c+m))


def _strip_fences(code):
    c = (code or "").strip()
    if c.startswith("```"):
        c = c.split("\n", 1)[1] if "\n" in c else c
        if c.rstrip().endswith("```"):
            c = c.rstrip()[:-3]
    return c


def diagnose_with_artifacts(test_code, test_name, log_tail, n=4):
    """Mirror of engine `diagnose()` (same prompt, same parser, same round-robin),
    but capturing the prompt sent + raw model response per hypothesis. Returns a
    list of dicts: {id, cause_class, explanation, patched_code, model, messages,
    raw_response}. Does not modify engine/."""
    models = _models_from_env()
    api_key = os.environ.get("FIREWORKS_API_KEY")
    if not api_key:
        raise ValueError("FIREWORKS_API_KEY not set; cannot run live diagnosis")
    from openai import OpenAI
    oai = OpenAI(base_url=BASE_URL, api_key=api_key)

    results = [None] * n

    def ask(i):
        hid = f"h{i + 1}"
        cause_hint = CAUSE_CLASSES[i % len(CAUSE_CLASSES)]
        model = models[i % len(models)]
        messages = _build_messages(test_code, test_name, log_tail, cause_hint)
        raw = None
        try:
            raw = _complete(oai, model, messages)
        except Exception as e:
            raw = f"__ERROR__: {e}"
        h = _parse_hypothesis(raw if not str(raw).startswith("__ERROR__") else None,
                              hid, fallback_cause=cause_hint, fallback_code=test_code)
        h["model"] = model
        h["cause_hint"] = cause_hint
        h["messages"] = messages
        h["raw_response"] = raw
        results[i] = h

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()
    return [h for h in results if h is not None]


def daytona_flake(code, sandboxes=4, per=4, sample_out=False, tag="cand"):
    """Measure a candidate file's flake rate on Daytona (fresh-process trials).
    Returns aggregate stats + full per-trial records."""
    code = _strip_fences(code) + "\n" + DRIVER
    results, sample, per_trial = {}, {}, []
    lock = threading.Lock()

    def worker(w):
        sb = None
        try:
            sb = client.create(CreateSandboxFromSnapshotParams(labels={"retrial": "penman-tourney"}), timeout=120)
            sbid = sb.id
            sb.process.exec("cat > /tmp/cand.py << 'PYEOF2'\n" + code + "\nPYEOF2")
            ins = sb.process.exec("pip install --quiet penman==1.2.1 2>&1 | tail -1; "
                                  "python3 -c 'import penman' && echo IMPORT_OK", timeout=180)
            if "IMPORT_OK" not in (ins.result or ""):
                with lock:
                    results[f"w{w}-install"] = f"ERR-INSTALL:{(ins.result or '')[:80]}"
                    per_trial.append({"sandbox": w, "sandbox_id": sbid, "trial": None,
                                      "verdict": "infra_error", "detail": (ins.result or "")[:120]})
                return
            for k in range(per):
                r = sb.process.exec("python3 /tmp/cand.py 2>&1; echo EXIT:$?", timeout=60)
                out = r.result or ""
                v = 1 if "EXIT:1" in out else 0 if "EXIT:0" in out else None
                with lock:
                    results[f"w{w}-t{k}"] = v
                    per_trial.append({"sandbox": w, "sandbox_id": sbid, "trial": k,
                                      "verdict": "fail" if v == 1 else "pass" if v == 0 else "indeterminate",
                                      "exit_seen": "1" if v == 1 else "0" if v == 0 else None})
                    if sample_out and "out" not in sample:
                        sample["out"] = out[:600]
        except Exception as e:
            with lock:
                results[f"w{w}-exc"] = f"ERR:{str(e)[:80]}"
                per_trial.append({"sandbox": w, "sandbox_id": None, "trial": None,
                                  "verdict": "infra_error", "detail": str(e)[:120]})
        finally:
            if sb:
                try: client.delete(client.get(sb.id))
                except Exception: pass

    ts = [threading.Thread(target=worker, args=(w,)) for w in range(sandboxes)]
    [t.start() for t in ts]; [t.join() for t in ts]
    vals = list(results.values())
    fails = sum(1 for v in vals if v == 1)
    ok = sum(1 for v in vals if v in (0, 1))
    errs = [v for v in vals if isinstance(v, str)]
    p, lo, hi = wilson(fails, ok)
    return {"trials": ok, "fails": fails, "errors": len(errs),
            "sandboxes_requested": sandboxes, "per_sandbox": per,
            "trials_phrasing": f"{ok} fresh-process trials across {sandboxes} sandboxes",
            "flake_rate": round(p, 3), "wilson_ci": [round(lo, 3), round(hi, 3)],
            "err_samples": errs[:3], "sample": sample.get("out", ""),
            "per_trial": sorted(per_trial, key=lambda d: (d["sandbox"], -1 if d["trial"] is None else d["trial"]))}


def main():
    t0 = time.monotonic()
    started_at = utc_now()
    models = _models_from_env()
    print("=== DIAGNOSIS (Fireworks, round-robin, SANITIZED repro) ===", flush=True)
    hyps = diagnose_with_artifacts(FLAKY_CODE, TEST_NAME, LOG_TAIL, n=4)
    print(f"generated {len(hyps)} hypotheses\n", flush=True)

    report = {
        "experiment": "penman test_rearrange — SANITIZED rerun (no cause hints in repro)",
        "started_at_utc": started_at,
        "repro_file": "seeds/real/penman_repro_sanitized.py",
        "test_name": TEST_NAME,
        "models": models,
        "note": ("Repro fed to the models contains NO root-cause hints (neutral docstring, "
                 "no seed/random commentary). Corrects the original run, whose repro header "
                 "named the cause. Winner selection = lowest flake rate whose Wilson CI upper "
                 "bound < the re-measured baseline rate, then a fresh confirmation round."),
        "hypotheses": [],
    }

    print("=== BASELINE: sanitized flaky repro (re-measured, unpatched) ===", flush=True)
    base = daytona_flake(FLAKY_CODE, sandboxes=4, per=4, tag="baseline")
    print(f"baseline: {base['fails']}/{base['trials']} fail = {base['flake_rate']:.0%} "
          f"CI{base['wilson_ci']} errors={base['errors']}\n", flush=True)
    report["baseline"] = base
    orig_rate = base["flake_rate"] if base["trials"] >= 8 else 0.88

    print("=== VERIFY each hypothesis (Daytona reruns) ===", flush=True)
    for h in hyps:
        res = daytona_flake(h["patched_code"], sandboxes=4, per=4, sample_out=True, tag=h["id"])
        patch = _strip_fences(h["patched_code"])
        seeds_random = ("random.seed" in patch)
        rec = {"id": h["id"], "model": h.get("model"), "cause_hint_given": h.get("cause_hint"),
               "cause_class": h["cause_class"], "explanation": h["explanation"],
               "adds_random_seed": seeds_random, "patch_len": len(patch),
               "prompt_messages": h["messages"], "raw_response": h["raw_response"],
               "extracted_patch": patch, **res}
        report["hypotheses"].append(rec)
        print(f"[{h['id']}] model={h.get('model','?').split('/')[-1]:16s} "
              f"cause={h['cause_class']:16s} rate={res['flake_rate']:.0%} "
              f"CI{res['wilson_ci']} err={res['errors']} seed_fix={seeds_random}", flush=True)
        print(f"     explanation: {h['explanation'][:150]}", flush=True)
        if res["err_samples"]:
            print(f"     err: {res['err_samples'][0]}", flush=True)
        print(flush=True)

    # winner = lowest flake rate whose CI upper bound < baseline rate
    cands = [r for r in report["hypotheses"]
             if r["trials"] >= 8 and r["wilson_ci"][1] < orig_rate]
    winner = min(cands, key=lambda r: r["flake_rate"]) if cands else None
    report["winner_id"] = winner["id"] if winner else None

    if winner:
        print(f"=== WINNER: {winner['id']} (rate {winner['flake_rate']:.0%}, "
              f"CI upper {winner['wilson_ci'][1]:.0%} < baseline {orig_rate:.0%}) ===", flush=True)
        print("=== CONFIRMATION ROUND (fresh independent sandboxes) ===", flush=True)
        wpatch = next(h["patched_code"] for h in hyps if h["id"] == winner["id"])
        conf = daytona_flake(wpatch, sandboxes=5, per=5, sample_out=True, tag="confirm")
        report["confirmation"] = conf
        print(f"confirm: {conf['fails']}/{conf['trials']} fail = {conf['flake_rate']:.0%} "
              f"CI{conf['wilson_ci']} errors={conf['errors']}", flush=True)
        print(f"\nwinner patch adds random.seed(): {winner['adds_random_seed']}", flush=True)
    else:
        print("=== NO WINNER (no hypothesis's CI upper bound cleared the baseline) ===", flush=True)

    # convergence summary: how many hypotheses seed the RNG AND verified as fixes
    seed_and_fix = [r for r in report["hypotheses"]
                    if r["adds_random_seed"] and r["trials"] >= 8 and r["wilson_ci"][1] < orig_rate]
    report["convergence"] = {
        "n_hypotheses": len(report["hypotheses"]),
        "n_add_random_seed": sum(1 for r in report["hypotheses"] if r["adds_random_seed"]),
        "n_seed_and_verified_fix": len(seed_and_fix),
        "seed_and_verified_ids": [r["id"] for r in seed_and_fix],
    }

    report["wallclock_s"] = round(time.monotonic() - t0, 1)
    report["finished_at_utc"] = utc_now()
    out = RETRIAL / "seeds/real/tournament_penman_sanitized_result.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nsaved {out.name}  ({report['wallclock_s']}s)", flush=True)


if __name__ == "__main__":
    main()
