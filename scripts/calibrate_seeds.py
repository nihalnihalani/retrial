"""GO/NO-GO calibration: measure each seed's empirical flake rate ON DAYTONA,
fresh sandbox per trial, 16-concurrent. Pick the seed landing 40-55% fail."""
import json, os, sys, time, threading, math
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

TRIALS = int(os.environ.get("TRIALS", "40"))
CONC = 16
client = Daytona(DaytonaConfig(target=os.environ.get("DAYTONA_TARGET", "us")))
seeds_dir = Path(__file__).parent.parent / "seeds"

def wilson(fails, n, z=1.96):
    if n == 0: return (0, 0, 1)
    p = fails / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    m = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (p, max(0, c-m), min(1, c+m))

def trial(code, results, i):
    sb = None
    try:
        sb = client.create(CreateSandboxFromSnapshotParams(labels={"retrial": "calib"}), timeout=120)
        sb.process.exec("cat > /tmp/seed.py << 'PYEOF'\n" + code + "\nPYEOF")
        r = sb.process.exec("python3 /tmp/seed.py; echo EXIT:$?", timeout=60)
        results[i] = 1 if "EXIT:1" in (r.result or "") else 0 if "EXIT:0" in (r.result or "") else None
    except Exception as e:
        results[i] = f"ERR:{str(e)[:60]}"
    finally:
        if sb:
            try: client.delete(client.get(sb.id))
            except Exception: pass

report = {}
for seed in sorted(seeds_dir.glob("test_*.py")):
    code = seed.read_text()
    results = {}
    t0 = time.monotonic()
    idx = 0
    while idx < TRIALS:
        batch = list(range(idx, min(idx + CONC, TRIALS)))
        ts = [threading.Thread(target=trial, args=(code, results, i)) for i in batch]
        [t.start() for t in ts]; [t.join() for t in ts]
        idx += CONC
    fails = sum(1 for v in results.values() if v == 1)
    ok = sum(1 for v in results.values() if v in (0, 1))
    errs = TRIALS - ok
    p, lo, hi = wilson(fails, ok) if ok else (0, 0, 1)
    verdict = "IDEAL" if 0.40 <= p <= 0.55 else "usable" if 0.15 <= p <= 0.75 else "REJECT"
    report[seed.name] = {"trials": ok, "errors": errs, "fails": fails,
                         "flake_rate": round(p, 3), "wilson_95": [round(lo, 3), round(hi, 3)],
                         "wallclock_s": round(time.monotonic() - t0, 1), "verdict": verdict}
    print(f"{seed.name}: {fails}/{ok} fail = {p:.0%} (95% CI {lo:.0%}-{hi:.0%}) "
          f"[{report[seed.name]['wallclock_s']}s] -> {verdict}", flush=True)

Path(__file__).parent.parent.joinpath("calibration-results.json").write_text(json.dumps(report, indent=2))
print("\nsaved calibration-results.json")
