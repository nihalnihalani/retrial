"""Calibrate the REAL penman test_rearrange flake ON DAYTONA.
Warm-pool with process isolation: pip install penman ONCE per sandbox,
then run the repro many times as fresh python3 processes (each = fresh
random entropy = the documented flake). exit0=pass exit1=fail."""
import os, sys, time, math, threading
from pathlib import Path
from dotenv import load_dotenv
RETRIAL = Path("/Users/nihalnihalani/Desktop/Github/retrial")
load_dotenv(RETRIAL / ".env")
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

SCR = Path(os.environ["SCR"])
REPRO = (SCR / "repro.py").read_text()
POOL = int(os.environ.get("POOL", "8"))
PER = int(os.environ.get("PER", "5"))   # trials per sandbox -> POOL*PER total
client = Daytona(DaytonaConfig(target=os.environ.get("DAYTONA_TARGET", "us")))

def wilson(f, n, z=1.96):
    if n == 0: return (0, 0, 1)
    p = f/n; d = 1+z*z/n
    c = (p+z*z/(2*n))/d
    m = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (p, max(0,c-m), min(1,c+m))

results = {}
lock = threading.Lock()

def worker(w):
    sb = None
    try:
        sb = client.create(CreateSandboxFromSnapshotParams(labels={"retrial":"penman"}), timeout=120)
        # write repro once
        sb.process.exec("cat > /tmp/repro.py << 'PYEOF2'\n" + REPRO + "\nPYEOF2")
        # install penman once (pinned to the studied version)
        ins = sb.process.exec("pip install --quiet penman==1.2.1 2>&1 | tail -1; python3 -c 'import penman,sys;print(\"OK\",penman.__version__)'", timeout=180)
        if "OK 1.2.1" not in (ins.result or ""):
            with lock: results[f"w{w}-install"] = f"ERR-INSTALL:{(ins.result or '')[:80]}"
            return
        for k in range(PER):
            r = sb.process.exec("python3 /tmp/repro.py; echo EXIT:$?", timeout=60)
            out = r.result or ""
            v = 1 if "EXIT:1" in out else 0 if "EXIT:0" in out else None
            with lock: results[f"w{w}-t{k}"] = v
    except Exception as e:
        with lock: results[f"w{w}-exc"] = f"ERR:{str(e)[:80]}"
    finally:
        if sb:
            try: client.delete(client.get(sb.id))
            except Exception: pass

t0 = time.monotonic()
ts = [threading.Thread(target=worker, args=(w,)) for w in range(POOL)]
[t.start() for t in ts]; [t.join() for t in ts]
vals = list(results.values())
fails = sum(1 for v in vals if v == 1)
ok = sum(1 for v in vals if v in (0,1))
errs = [v for v in vals if isinstance(v, str)]
p, lo, hi = wilson(fails, ok)
print(f"\npenman test_rearrange (real, unseeded random branch)")
print(f"trials={ok} fails={fails} flake_rate={p:.0%} wilson95=[{lo:.0%},{hi:.0%}] "
      f"errors={len(errs)} wallclock={time.monotonic()-t0:.1f}s")
for e in errs[:5]: print("  ", e)
import json
(SCR/"penman_daytona_result.json").write_text(json.dumps(
    {"trials":ok,"fails":fails,"flake_rate":round(p,3),"wilson_95":[round(lo,3),round(hi,3)],
     "errors":len(errs),"wallclock_s":round(time.monotonic()-t0,1),
     "pool":POOL,"per_sandbox":PER}, indent=2))
print("saved penman_daytona_result.json")
