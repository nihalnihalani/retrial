"""FlakeBisector: time-travel bisection of order-dependency / shared-state flakes.

The idea: a suite that poisons one of its own tests is a hidden edge in the
test graph. We make it visible by CHECKPOINTING the suite at every test
boundary — run test i in a live root sandbox, then fork+pause the root
(the Rewind checkpoint primitive: a paused fork-child captures the full
filesystem + RAM while the root keeps running) — and then rerunning ONLY the
suspect from each checkpoint, many times, with the same Wilson-CI oracle the
tournament uses. Binary search over checkpoints finds the exact test after
which the suspect's empirical flake rate flips: the polluter.

Checkpoint indexing convention: checkpoint `k` = suite state after the first
`k` tests have run (`k = 0` is pristine, before anything ran). If the flip
lies between checkpoints `k` and `k+1`, the polluter is suite test index `k`
(0-based).

Assumptions and honest limitations (also stated in the CLI --help epilog):

- Monotonicity: the search assumes the suspect's true flake rate is a
  monotonic step function across checkpoints (pristine ... pristine, polluted
  ... polluted). A single noisy probe (min_trials is small and `verify`
  early-stops) could flip the wrong way, so after convergence BOTH sides of
  the flip are re-probed with the full trial budget (early-stop defeated). If
  the confirmation contradicts the search, the run reports an honest
  inconclusive instead of a confidently wrong culprit.
- Fork-capable Daytona is REQUIRED. Bisection has no snapshot fallback — the
  capability IS the fork; without checkpoints there is nothing to travel to.
  On any fork-path failure `run()` reports the error honestly (a terminal
  `bisect_done {"error": ...}`), never a fake result.
- Residual risk: forking a started checkpoint while it is being probed is
  mock-verified only (no live keys exist in this environment). Fork issuance
  is serialized behind a lock (see `_CheckpointProbePool.lease`) because the
  only proven `_experimental_fork` usage is strictly sequential from a single
  thread; if the live SDK still rejects the pattern, the error path above
  reports it honestly.
"""
import base64
import re
import threading
import time
from uuid import uuid4

from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

from .config import DEFAULT_THRESHOLD
from .forkpool import _retry
from .registry import as_safe
from .settings import get_settings
from .verifier import verify

_EXIT_RE = re.compile(r"EXIT:(-?\d+)")


class _CheckpointProbePool:
    """lease/release adapter over ONE started checkpoint.

    Duck-types the two methods `verifier.verify()` actually calls (lease and
    release), so `verify` is reused VERBATIM as the per-checkpoint oracle —
    adaptive early-stop, Wilson CI, verdict table and all. Probe forks are
    single-use: `verify` is invoked with isolation="sandbox", so it never
    marks them reusable.
    """

    def __init__(self, ckpt, client, registry=None):
        self._ckpt = ckpt
        self._client = client
        self._registry = as_safe(registry)
        # CONCURRENCY RULE (load-bearing — do not "optimize" this lock away):
        # verify() calls lease() from up to `conc` worker threads at once, but
        # the ONLY proven usage of _experimental_fork is strictly sequential
        # from a single thread on one handle (Rewind's fork_futures). No code
        # anywhere demonstrates concurrent forks on one sandbox handle, and the
        # real SDK cannot be exercised here. So fork issuance is serialized:
        # worker threads queue on this lock and forks go out one at a time.
        # Trial EXECUTION afterwards still runs fully parallel — the cost is
        # only the (small) serial fork latency, not trial serialization.
        self._fork_lock = threading.Lock()
        self._lock = threading.Lock()   # state lock (separate from fork issuance)
        self._live = {}                 # id -> probe fork
        self._bg = []                   # in-flight background delete threads

    def lease(self):
        with self._fork_lock:
            fork = _retry("probe.fork", lambda: self._ckpt._experimental_fork(
                name=f"retrial-probe-{uuid4().hex[:6]}"))
        with self._lock:
            self._live[fork.id] = fork
        # Observatory hook OUTSIDE the fork/state locks: a single-use probe
        # clone, child of the checkpoint being probed. No destroy_fn — probes
        # die inside this pool's destroy_all, never via DELETE.
        self._registry.register(fork, role="bisect-probe", backend="fork",
                                parent_id=getattr(self._ckpt, "id", None),
                                state="warm", destroy_fn=None)
        return fork

    def release(self, sb, reusable=False):
        # Probe forks are never reused regardless of the flag: every probe
        # trial must start from the checkpoint's exact fs+RAM state, and a
        # used fork is by definition no longer that state.
        if sb is None:
            return
        t = threading.Thread(target=self._destroy, args=(sb,), daemon=True)
        with self._lock:
            self._bg.append(t)
        t.start()

    def _destroy(self, sb):
        # Pop-first so a release racing destroy_all deletes exactly once —
        # whoever wins the pop owns the deletion; the loser is a no-op.
        with self._lock:
            owned = self._live.pop(getattr(sb, "id", None), None)
        if owned is None:
            return
        try:
            self._client.delete(self._client.get(sb.id))
        except Exception:
            pass
        # The pop winner owns the deletion — mark destroyed exactly once.
        self._registry.mark_destroyed(getattr(sb, "id", None))

    def destroy_all(self):
        """Delete every leftover probe fork; joins in-flight background
        deletes first so the checkpoint is safe to pause/delete afterwards
        (Daytona refuses to delete a parent while fork-children live)."""
        with self._lock:
            bg = list(self._bg)
            self._bg.clear()
        for t in bg:
            t.join(timeout=30)
        with self._lock:
            leftovers = list(self._live.values())
        for sb in leftovers:
            self._destroy(sb)
        return len(leftovers)


class FlakeBisector:
    """Checkpoint a suite at test boundaries and bisect to the polluting test."""

    def __init__(self, client=None, target=None, bus=None, labels=None,
                 max_trials=30, conc=8, threshold=DEFAULT_THRESHOLD,
                 min_trials=8, timeout=60, auto_delete_min=None, registry=None):
        # Own root sandbox lifecycle, NOT via the pool: bisection needs direct
        # fork control over one long-lived root (same client construction and
        # create kwargs as SandboxPool._create_one).
        self._client = client or Daytona(
            DaytonaConfig(target=target or get_settings().resolved_pool_target())
        )
        self._labels = labels or {"retrial": "bisect"}
        self._auto_delete_min = (auto_delete_min if auto_delete_min is not None
                                 else get_settings().auto_delete_min)
        self._bus = bus
        # Sandbox Observatory feed: root/checkpoint/probe lifecycle + root execs
        # are mirrored into the registry (shared with the probe pools). as_safe
        # makes even a hostile injected registry unable to break the bisect.
        self._registry = as_safe(registry)
        self.max_trials = max_trials
        self.conc = conc
        self.threshold = threshold
        self.min_trials = min_trials
        self.timeout = timeout
        self._root = None
        self._ckpts = []        # index k = suite state after the first k tests
        self._probes = {}       # k -> latest verify() result for checkpoint k
        self._probe_pool = None  # the probe pool currently in flight, if any
        self._suspect_code = None
        # Real (no longer declared-but-unused): guards every mutation/swap of
        # _ckpts / _root / _probe_pool against a concurrent reader.
        self._lock = threading.Lock()
        # Cooperative-cancel channel. A forced destroy_all on the server sets
        # this via cancel(); _run() checks it at each loop boundary and raises,
        # so teardown happens in the RUN thread's own finally (leaf-first) —
        # the server never yanks an active bisector's resources out from under
        # its unsynchronized probe loop. See OBSERVATORY-PLAN.md B4.
        self._cancelled = threading.Event()

    # -- cooperative cancel ----------------------------------------------
    def cancel(self):
        """Request cooperative cancellation (called by the server on a forced
        reap). _run() checks this at each loop boundary and raises; teardown
        then happens in the run thread's own finally — never from the caller."""
        self._cancelled.set()

    def _raise_if_cancelled(self):
        if self._cancelled.is_set():
            raise RuntimeError("bisect cancelled by destroy_all(force)")

    # -- internals -------------------------------------------------------
    def _emit(self, event_type, payload):
        if self._bus is not None:
            try:
                self._bus.emit(event_type, payload)
            except Exception:
                pass

    def _exec_test(self, sandbox, code):
        """Run one suite test in the (live) root sandbox — the trial.py
        single-round-trip pattern: b64 -> /tmp/seed.py -> EXIT:$? -> parse.
        Returns the same result dict shape as run_trial."""
        t0 = time.monotonic()
        sid = getattr(sandbox, "id", None)
        cmd = None
        try:
            b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
            cmd = (f"echo '{b64}' | base64 -d > /tmp/seed.py && "
                   f"python3 /tmp/seed.py; echo EXIT:$?")
            # Observatory: the root sandbox is running a suite test (registry
            # @_safe — never affects the bisect).
            self._registry.exec_started(sid, cmd)
            r = sandbox.process.exec(cmd, timeout=self.timeout)
            out = r.result or ""  # .result may be None (rewind lesson)
            duration = time.monotonic() - t0
            # LAST match: the marker is our final line, but the suite's own
            # stdout precedes it and must not be able to forge a verdict.
            # Same rule as trial.py — keep the two in step.
            _ms = _EXIT_RE.findall(out)
            m = _ms[-1] if _ms else None
            if m is None:
                log_tail = out.strip()[-500:]
                self._registry.exec_finished(sid, cmd, None, log_tail,
                                             round(duration, 3))
                return {"passed": False, "duration_s": round(duration, 3),
                        "log_tail": log_tail, "exit_code": None,
                        "error": "no EXIT marker in output"}
            code_n = int(m)
            log_tail = out.strip()[-500:]
            self._registry.exec_finished(sid, cmd, code_n, log_tail,
                                         round(duration, 3))
            return {"passed": code_n == 0, "duration_s": round(duration, 3),
                    "log_tail": log_tail, "exit_code": code_n,
                    "error": None}
        except Exception as e:
            duration = round(time.monotonic() - t0, 3)
            self._registry.exec_finished(sid, cmd, None, str(e)[-500:], duration)
            return {"passed": False,
                    "duration_s": duration,
                    "log_tail": str(e)[-500:], "exit_code": None,
                    "error": str(e)[:200]}

    def _create_root(self):
        kwargs = {"labels": self._labels}
        if self._auto_delete_min and self._auto_delete_min > 0:
            kwargs["auto_delete_interval"] = self._auto_delete_min
        root = self._client.create(CreateSandboxFromSnapshotParams(**kwargs),
                                   timeout=120)
        with self._lock:
            self._root = root
        # Observatory: the bisect root is the trunk of the checkpoint tree.
        self._registry.register(root, role="root", backend="fork",
                                labels=self._labels, state="creating")
        # Pay the cold start now so per-test exec timings are honest.
        try:
            root.process.exec("echo warm")
        except Exception:
            pass
        self._registry.set_state(getattr(root, "id", None), "warm")

    def _checkpoint(self, label, test_passed=None):
        """Freeze the current root state as the next checkpoint: fork the live
        root (the root never stops — the Rewind invariant), pause the child."""
        fork = _retry("bisect.ckpt.fork", lambda: self._root._experimental_fork(
            name=f"retrial-bisect-ckpt-{uuid4().hex[:6]}"))
        _retry("bisect.ckpt.pause", fork.pause)
        with self._lock:
            self._ckpts.append(fork)
            k = len(self._ckpts) - 1
        # Observatory: a frozen checkpoint, child of the root.
        self._registry.register(fork, role="checkpoint", backend="fork",
                                parent_id=getattr(self._root, "id", None),
                                labels=self._labels, state="paused")
        payload = {"k": k, "label": label}
        if test_passed is not None:
            payload["test_passed"] = test_passed  # informational only
        self._emit("checkpoint_created", payload)

    def _disjoint(self, a_ci, b_ci):
        """The coordinator's CI-overlap criterion, inverted: True when the two
        Wilson intervals share no point — the flake rate provably flipped."""
        return a_ci[1] < b_ci[0] or b_ci[1] < a_ci[0]

    def _probe(self, k, min_trials=None, max_trials=None, use_cache=True):
        """Fork probe clones off checkpoint k and measure the suspect's flake
        rate there with the verify() oracle. Results are cached per checkpoint;
        the confirmation pass bypasses the cache (use_cache=False) and defeats
        early-stop by passing min_trials == max_trials."""
        self._raise_if_cancelled()   # cooperative-cancel boundary: no new probe
        if use_cache and k in self._probes:
            return self._probes[k]
        with self._lock:
            ckpt = self._ckpts[k]
        _retry("probe.start", ckpt.start)
        # Observatory: the checkpoint is awake while probed, back to paused after.
        self._registry.set_state(getattr(ckpt, "id", None), "warm")
        probe_pool = _CheckpointProbePool(ckpt, self._client,
                                          registry=self._registry)
        with self._lock:
            self._probe_pool = probe_pool
        try:
            res = verify(probe_pool, self._suspect_code,
                         max_trials=max_trials if max_trials is not None else self.max_trials,
                         conc=self.conc, threshold=self.threshold,
                         min_trials=min_trials if min_trials is not None else self.min_trials,
                         bus=None, timeout=self.timeout,
                         isolation="sandbox", emit_trials=False)
        finally:
            # Leaf-first, always: probe forks die before the checkpoint is
            # touched, and the checkpoint ALWAYS returns to frozen (the
            # fork_futures invariant) — even when a probe raised mid-verify.
            try:
                probe_pool.destroy_all()
            except Exception:
                pass
            with self._lock:
                self._probe_pool = None
            try:
                ckpt.pause()
            except Exception:
                pass
            self._registry.set_state(getattr(ckpt, "id", None), "paused")
        self._probes[k] = res  # confirmation results overwrite: fresher, bigger budget
        self._emit("checkpoint_probed", {
            "k": k,
            "flake_rate": res["flake_rate"],
            "wilson_ci": res["wilson_ci"],
            "trials": res["trials"],
            "verdict": res["verdict"],
        })
        return res

    def _probe_summary(self, k):
        res = self._probes.get(k)
        if res is None:
            return None
        return {"k": k, "flake_rate": res["flake_rate"],
                "wilson_ci": res["wilson_ci"], "trials": res["trials"],
                "verdict": res["verdict"]}

    def _probe_table(self):
        return [self._probe_summary(k) for k in sorted(self._probes)]

    # -- public API ------------------------------------------------------
    def run(self, suite, suspect_index=None, suite_name=""):
        """Bisect `suite` = [(name, code), ...] to the test that pollutes the
        suspect (default: the last entry; the prefix is everything before it).

        Returns the terminal `bisect_done` payload: {"polluter_test": name,
        "polluter_index": k, ...} on success, {"polluter_test": None,
        "reason": ...} on an honest inconclusive, {"error": ...} on failure.
        Degrade-gracefully: never raises to the caller.
        """
        try:
            return self._run(suite, suspect_index, suite_name)
        except Exception as e:
            payload = {"error": str(e)[:200]}
            self._emit("bisect_done", payload)
            return payload
        finally:
            self.destroy_all()

    def _run(self, suite, suspect_index, suite_name):
        if not suite:
            raise ValueError("empty suite: nothing to bisect")
        if suspect_index is None:
            suspect_index = len(suite) - 1
        suspect_name, self._suspect_code = suite[suspect_index]
        prefix = suite[:suspect_index]
        big_k = len(prefix)  # checkpoints run 0..big_k

        self._emit("bisect_started", {
            "suite": suite_name, "n_tests": big_k, "suspect": suspect_name,
            "max_trials": self.max_trials,
        })

        # Forward pass: root runs the prefix, freezing a checkpoint after
        # every test boundary. The root itself never stops.
        self._raise_if_cancelled()
        self._create_root()
        self._checkpoint("pristine")  # checkpoint 0
        for name, code in prefix:
            # Cooperative-cancel boundary: a forced reap stops the forward pass
            # before building the next checkpoint (teardown runs in this thread's
            # own finally, leaf-first).
            self._raise_if_cancelled()
            res = self._exec_test(self._root, code)
            # The prefix test's own pass/fail is informational (a polluter is
            # usually green itself) — logged into the event, never a gate.
            self._checkpoint(name, test_passed=res["passed"])

        # Probe the endpoints first: no flip across the whole suite means no
        # order dependency is detectable — say so instead of fishing.
        base = self._probe(0)
        full = self._probe(big_k)
        base_ci = base["wilson_ci"]
        if not self._disjoint(base_ci, full["wilson_ci"]):
            payload = {
                "polluter_test": None,
                "reason": "flake rate does not flip across the suite",
                "base": self._probe_summary(0),
                "full": self._probe_summary(big_k),
            }
            self._emit("bisect_done", payload)
            return payload

        # Binary search. Invariant: lo behaves like base, hi like full.
        lo, hi = 0, big_k
        while hi - lo > 1:
            mid = (lo + hi) // 2
            r = self._probe(mid)
            flipped = self._disjoint(base_ci, r["wilson_ci"])
            if flipped:
                hi = mid
            else:
                lo = mid
            self._emit("bisect_narrowed",
                       {"lo": lo, "hi": hi, "k": mid, "flipped": flipped})

        # Confirmation pass (noise armor): the search assumes a monotonic step
        # function; a single noisy early-stopped probe could flip the wrong
        # way. Re-probe BOTH sides of the flip with the full budget
        # (min_trials == max_trials defeats early-stop), bypassing the cache.
        confirm_lo = self._probe(lo, min_trials=self.max_trials,
                                 max_trials=self.max_trials, use_cache=False)
        confirm_hi = self._probe(hi, min_trials=self.max_trials,
                                 max_trials=self.max_trials, use_cache=False)
        lo_like_base = not self._disjoint(base_ci, confirm_lo["wilson_ci"])
        hi_flipped = self._disjoint(base_ci, confirm_hi["wilson_ci"])
        if not (lo_like_base and hi_flipped):
            # An honest inconclusive beats a confidently wrong culprit.
            payload = {
                "polluter_test": None,
                "reason": ("confirmation pass contradicts search (noisy probe); "
                           "rerun with higher --max-trials"),
                "probes": self._probe_table(),
            }
            self._emit("bisect_done", payload)
            return payload

        # The flip sits between checkpoints lo and hi == lo+1, so the polluter
        # is the test that ran between them: prefix[lo].
        polluter_name = prefix[lo][0]
        payload = {
            "polluter_test": polluter_name,
            "polluter_index": lo,
            "suspect": suspect_name,
            "checkpoints": big_k + 1,
            "probes": self._probe_table(),
            "base_flake_rate": base["flake_rate"],
            "full_flake_rate": full["flake_rate"],
            "confirmed": True,
        }
        self._emit("bisect_done", payload)
        return payload

    def destroy_all(self):
        """Leaf-first teardown, idempotent: probe leftovers (children of a
        checkpoint), then every checkpoint (children of root), then root. Swaps
        of _probe_pool / _ckpts / _root are done under _lock so a concurrent
        reader (e.g. a probe still indexing _ckpts) never sees a half-swapped
        state."""
        count = 0
        with self._lock:
            pool, self._probe_pool = self._probe_pool, None
        if pool is not None:
            try:
                count += pool.destroy_all()
            except Exception:
                pass
        with self._lock:
            ckpts, self._ckpts = self._ckpts, []
        for ck in ckpts:
            try:
                self._client.delete(self._client.get(ck.id))
            except Exception:
                pass
            self._registry.mark_destroyed(getattr(ck, "id", None))
            count += 1
        with self._lock:
            root, self._root = self._root, None
        if root is not None:
            try:
                self._client.delete(self._client.get(root.id))
            except Exception:
                pass
            self._registry.mark_destroyed(getattr(root, "id", None))
            count += 1
        return count
