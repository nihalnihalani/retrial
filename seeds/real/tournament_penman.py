"""ULTIMATE EXPERIMENT — run a full Retrial tournament on the REAL penman flake
(goodmami/penman tests/test_layout.py::test_rearrange, IDoFT NOD, fix = PR #102).

Question: do the Fireworks models independently rediscover the maintainer's fix
(make the `random` seed effective before rearrange(t, model.random_order))?

This is a STANDALONE script. It IMPORTS the engine's diagnosis as-is
(`from retrial.diagnosis import diagnose`) but does NOT touch engine/ files.
The engine's pool/trial run bare-container scripts and can't pip-install penman,
so the verify/confirm loops reuse the calibration-harness pattern from
seeds/real/calibrate_penman.py: create sandboxes, `pip install penman==1.2.1`
once per sandbox, then rerun the candidate file as fresh python3 processes
(each fresh process = fresh random entropy = the flake). Wilson 95% CI throughout.

Run:  .venv/bin/python seeds/real/tournament_penman.py
"""
import os, sys, json, math, time, threading
from pathlib import Path
from dotenv import load_dotenv

RETRIAL = Path("/Users/nihalnihalani/Desktop/Github/retrial")
SCR = Path("/private/tmp/claude-501/-Users-nihalnihalani-Desktop-Github-dyt-hack-sprint-/4127cf46-b5ce-4782-9d73-f6298c89a856/scratchpad")
load_dotenv(RETRIAL / ".env")
sys.path.insert(0, str(RETRIAL / "engine"))

from retrial.diagnosis import diagnose          # engine, imported as-is
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

client = Daytona(DaytonaConfig(target=os.environ.get("DAYTONA_TARGET", "us")))

# --- inputs: the flaky repro (unseeded) + a real failing run log ---
FLAKY_CODE = (RETRIAL / "seeds/real/penman_test_rearrange_repro.py").read_text()
LOG_TAIL = (SCR / "fail_log.txt").read_text()
TEST_NAME = "tests/test_layout.py::test_rearrange"
ORIGINAL_RATE = 0.88   # measured earlier (35/40 on Daytona); re-measured below too

# A safety-net driver appended to each candidate: only fires if the candidate
# did NOT already sys.exit (sys.exit terminates first, so sys.exit-style patches
# are untouched). Handles pytest-style patches that merely DEFINE test funcs.
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
    _s.exit(0)
"""


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


def daytona_flake(code, sandboxes=4, per=4, sample_out=False, tag="cand"):
    """Measure a candidate file's flake rate on Daytona (fresh-process trials).
    Returns {trials, fails, errors, flake_rate, wilson_ci, sample}."""
    code = _strip_fences(code) + "\n" + DRIVER
    results, sample = {}, {}
    lock = threading.Lock()

    def worker(w):
        sb = None
        try:
            sb = client.create(CreateSandboxFromSnapshotParams(labels={"retrial": "penman-tourney"}), timeout=120)
            sb.process.exec("cat > /tmp/cand.py << 'PYEOF2'\n" + code + "\nPYEOF2")
            ins = sb.process.exec("pip install --quiet penman==1.2.1 2>&1 | tail -1; "
                                  "python3 -c 'import penman' && echo IMPORT_OK", timeout=180)
            if "IMPORT_OK" not in (ins.result or ""):
                with lock: results[f"w{w}-install"] = f"ERR-INSTALL:{(ins.result or '')[:80]}"
                return
            for k in range(per):
                r = sb.process.exec("python3 /tmp/cand.py 2>&1; echo EXIT:$?", timeout=60)
                out = r.result or ""
                v = 1 if "EXIT:1" in out else 0 if "EXIT:0" in out else None
                with lock:
                    results[f"w{w}-t{k}"] = v
                    if sample_out and "out" not in sample:
                        sample["out"] = out[:600]
        except Exception as e:
            with lock: results[f"w{w}-exc"] = f"ERR:{str(e)[:80]}"
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
            "flake_rate": round(p, 3), "wilson_ci": [round(lo, 3), round(hi, 3)],
            "err_samples": errs[:3], "sample": sample.get("out", "")}


def main():
    t0 = time.monotonic()
    print("=== DIAGNOSIS (Fireworks, 4 models round-robin) ===", flush=True)
    hyps = diagnose(FLAKY_CODE, TEST_NAME, LOG_TAIL, n=4)
    print(f"generated {len(hyps)} hypotheses\n", flush=True)

    report = {"original_rate_prior": ORIGINAL_RATE, "hypotheses": []}

    # re-measure the original (unpatched) flaky repro for apples-to-apples baseline
    print("=== BASELINE: original flaky repro (re-measured) ===", flush=True)
    base = daytona_flake(FLAKY_CODE, sandboxes=4, per=4, tag="baseline")
    print(f"baseline: {base['fails']}/{base['trials']} fail = {base['flake_rate']:.0%} "
          f"CI{base['wilson_ci']} errors={base['errors']}\n", flush=True)
    report["baseline"] = base
    orig_rate = base["flake_rate"] if base["trials"] >= 8 else ORIGINAL_RATE

    print("=== VERIFY each hypothesis (Daytona reruns) ===", flush=True)
    for h in hyps:
        res = daytona_flake(h["patched_code"], sandboxes=4, per=4, sample_out=True, tag=h["id"])
        patch = _strip_fences(h["patched_code"])
        seeds_random = ("random.seed" in patch)
        rec = {"id": h["id"], "model": h.get("model"), "cause_class": h["cause_class"],
               "explanation": h["explanation"], "adds_random_seed": seeds_random,
               "patch_len": len(patch), **res}
        report["hypotheses"].append(rec)
        print(f"[{h['id']}] model={h.get('model','?').split('/')[-1]:14s} "
              f"cause={h['cause_class']:16s} rate={res['flake_rate']:.0%} "
              f"CI{res['wilson_ci']} err={res['errors']} seed_fix={seeds_random}", flush=True)
        print(f"     explanation: {h['explanation'][:150]}", flush=True)
        if res["err_samples"]:
            print(f"     err: {res['err_samples'][0]}", flush=True)
        print(flush=True)

    # winner = lowest flake rate whose CI upper bound < original rate
    cands = [r for r in report["hypotheses"]
             if r["trials"] >= 8 and r["wilson_ci"][1] < orig_rate]
    winner = min(cands, key=lambda r: r["flake_rate"]) if cands else None
    report["winner_id"] = winner["id"] if winner else None

    if winner:
        print(f"=== WINNER: {winner['id']} (rate {winner['flake_rate']:.0%}, "
              f"CI upper {winner['wilson_ci'][1]:.0%} < original {orig_rate:.0%}) ===", flush=True)
        print("=== CONFIRMATION ROUND (fresh independent sandboxes) ===", flush=True)
        # rebuild winner's patched code from the matching hypothesis
        wpatch = next(h["patched_code"] for h in hyps if h["id"] == winner["id"])
        conf = daytona_flake(wpatch, sandboxes=5, per=5, sample_out=True, tag="confirm")
        report["confirmation"] = conf
        print(f"confirm: {conf['fails']}/{conf['trials']} fail = {conf['flake_rate']:.0%} "
              f"CI{conf['wilson_ci']} errors={conf['errors']}", flush=True)
        print(f"\nwinner patch adds random.seed(): {winner['adds_random_seed']}", flush=True)
    else:
        print("=== NO WINNER (no hypothesis's CI upper bound cleared the original) ===", flush=True)

    report["wallclock_s"] = round(time.monotonic() - t0, 1)
    (RETRIAL / "seeds/real/tournament_penman_result.json").write_text(json.dumps(report, indent=2))
    # also persist the winning patch for the report
    if winner:
        wpatch = next(h["patched_code"] for h in hyps if h["id"] == winner["id"])
        (SCR / "winner_patch.py").write_text(_strip_fences(wpatch))
    print(f"\nsaved tournament_penman_result.json  ({report['wallclock_s']}s)", flush=True)


if __name__ == "__main__":
    main()
