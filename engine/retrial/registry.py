"""SandboxRegistry: one thread-safe ledger of every sandbox Retrial ever touches.

This is the backend of the Sandbox Observatory — the judge-facing window into
the Daytona swarm. The pools (SandboxPool, ForkSandboxPool) and the
FlakeBisector call registry hooks at every create / fork / pause / start / exec
/ destroy boundary, and the registry turns those into a live snapshot (with the
fork-lineage tree) plus a stream of typed EventBus events over /ws.

Two non-negotiable rules govern this module:

1. OBSERVABILITY MUST NEVER BREAK A RUN. Every public mutator is wrapped in the
   `_safe` decorator, which swallows EVERYTHING and returns None. A pool wired
   to a registry whose methods raise (a broken/absent registry) behaves
   byte-identically to one with no registry at all — proven by the
   RaisingRegistry tests. There is no try/except at the call sites: one
   mechanism, one place.

2. THE REGISTRY IS NEVER RESET AT RUN ACCEPTANCE. Live sandboxes and the
   total_ever / destroyed counters genuinely span runs, because the pool itself
   is shared and pre-warmed across runs (see OBSERVATORY-PLAN.md, the
   "stale-bleed lesson"). Wiping it would orphan real sandboxes from the UI and
   lie about totals. What IS per-run is the bus ring buffer; so _accept_run()
   re-emits a fresh `registry_snapshot` right after BUS.reset(), re-seeding the
   new run's replay buffer with the current sandbox world — inside _run_lock,
   never from a background thread.

Lock discipline (see the comment on _lock): registry methods never call
pool/bisector code while holding _lock. Only `destroy()` calls out, and it
snapshots the destroyer then releases the lock first. Pool code calls registry
hooks only OUTSIDE its own _lock scopes. Lock order is therefore strictly
one-directional per call (pool-lock -> release -> registry-lock), so no
deadlock is possible. An RLock is used because a future refactor could re-enter
a @_safe-wrapped path that already holds it — cheap insurance, zero cost.
"""
import collections
import functools
import os
import threading
import time


def _safe(fn):
    """Observability must never break a run: swallow EVERYTHING.

    Every public mutator on SandboxRegistry is wrapped with this so a hook that
    raises (a bug here, a broken injected registry, a handle that vanished)
    degrades to a no-op instead of taking down the trial/tournament that called
    it. Pure readers are NOT wrapped — they may not raise by construction.
    """
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None
    return wrapper


class _SafeProxy:
    """Wrap any registry-like object so EVERY method call is swallowed.

    The `@_safe` decorator already guarantees the real SandboxRegistry never
    raises, but the pools/bisector accept an INJECTED registry (tests, or a
    future custom sink). Wrapping it here — the SAME never-break-a-run mechanism,
    still in one place — means a broken or hostile registry can never take down
    the run that hooked it, without scattering try/except across every call
    site. Wrapping the already-safe default is harmless (a cheap double-guard).
    """
    __slots__ = ("_t",)

    def __init__(self, target):
        object.__setattr__(self, "_t", target)

    def __getattr__(self, name):
        try:
            attr = getattr(object.__getattribute__(self, "_t"), name)
        except Exception:
            return lambda *a, **k: None
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        def wrapper(*a, **k):
            try:
                return attr(*a, **k)
            except Exception:
                return None
        return wrapper


def as_safe(registry):
    """Return a never-raising view of `registry` for pool/bisector wiring. None
    means the process-default REGISTRY (itself already @_safe)."""
    target = registry if registry is not None else REGISTRY
    return _SafeProxy(target)


# The wire-shape of a `sandbox_registered` payload (mirrored verbatim by
# ui/src/types.ts::SandboxRecordWire). Kept as a tuple so both register() and
# the snapshot path stay in sync on the exact key set.
_REGISTERED_KEYS = ("id", "role", "backend", "state", "parent_id",
                    "created_ts", "labels", "isolation", "exec_count",
                    "preview_url")


class SandboxRegistry:
    """Thread-safe ledger of every sandbox the system ever touches."""

    def __init__(self, bus=None, exec_history=None, destroyed_retain=None):
        # Per-sandbox recent-exec ring depth and the destroyed-record retention
        # window, both env-overridable and read at construction (retrial style).
        self._exec_history = (exec_history if exec_history is not None
                              else int(os.environ.get("RETRIAL_EXEC_HISTORY", "20")))
        self._destroyed_retain = (destroyed_retain if destroyed_retain is not None
                                  else int(os.environ.get("RETRIAL_DESTROYED_RETAIN", "50")))
        # See the module docstring for lock discipline. Never held across a call
        # into pool/bisector code (only destroy() calls out, lock released first).
        self._lock = threading.RLock()
        self._records = {}       # id -> record dict (destroyed retained bounded)
        self._handles = {}       # id -> live sandbox object (dropped on destroy)
        self._destroyers = {}    # id -> destroy_fn(sid) supplied by the owner
        self._children = {}      # parent_id -> [child ids]  (lineage index)
        self._destroyed_order = collections.deque()  # destroyed ids, oldest first
        self._bus = bus
        # Exact FOREVER — bumped at register/destroy, never recomputed from the
        # (bounded, pruned) record map. This is what keeps the header counters
        # honest across a multi-hour demo day even as destroyed records age out.
        self._total_ever = 0
        self._destroyed = 0
        self._t0 = time.monotonic()

    # -- internals -------------------------------------------------------
    def _now(self):
        return round(time.monotonic() - self._t0, 4)

    def _emit(self, event_type, payload):
        """Fan a registry event onto the bus, best-effort. Never raises.

        Called only OUTSIDE self._lock (payload is built under the lock, emitted
        after release) so a subscriber can never deadlock against a mutator.
        """
        bus = self._bus
        if bus is None:
            return
        try:
            bus.emit(event_type, payload)
        except Exception:
            pass

    def _registered_payload(self, rec):
        out = {k: rec[k] for k in _REGISTERED_KEYS}
        out["labels"] = dict(rec["labels"])
        return out

    def _snapshot_record(self, rec):
        # Full record minus the recent_execs ring (that rides only on
        # GET /sandboxes/{id}); labels copied so a later mutation can't bleed.
        out = {k: v for k, v in rec.items() if k != "recent_execs"}
        out["labels"] = dict(rec["labels"])
        return out

    def _depth_locked(self, sid):
        """Lineage depth (0 = root). Caller MUST hold self._lock. Cycle-safe."""
        depth = 0
        seen = set()
        cur = self._records.get(sid)
        while cur is not None and cur.get("parent_id") is not None:
            pid = cur["parent_id"]
            if pid in seen:
                break
            seen.add(pid)
            depth += 1
            cur = self._records.get(pid)
        return depth

    def _prune_destroyed_locked(self):
        """Bound snapshot growth: keep only the `destroyed_retain` most-recently
        destroyed records. Caller MUST hold self._lock. The int counters are
        untouched (they are exact forever); only the record MAP is pruned."""
        while len(self._destroyed_order) > self._destroyed_retain:
            old = self._destroyed_order.popleft()
            oldrec = self._records.pop(old, None)
            self._children.pop(old, None)          # its own child-list slot
            self._handles.pop(old, None)
            self._destroyers.pop(old, None)
            if oldrec is not None:
                pid = oldrec.get("parent_id")
                if pid is not None and pid in self._children:
                    try:
                        self._children[pid].remove(old)
                    except ValueError:
                        pass
                    if not self._children[pid]:
                        del self._children[pid]

    # -- wiring ----------------------------------------------------------
    @_safe
    def attach_bus(self, bus):
        """The server wires the process BUS once at import (see server.py)."""
        self._bus = bus

    # -- lifecycle hooks (all @_safe) ------------------------------------
    @_safe
    def register(self, sb, role, backend, parent_id=None, labels=None,
                 isolation=None, destroy_fn=None, state="creating"):
        """Record a newly created/forked sandbox, index its lineage, store its
        handle + destroyer, bump total_ever, and emit `sandbox_registered`.

        Idempotent on the id: a second register for the same id refreshes the
        handle/destroyer but never double-counts total_ever (the byte-identical
        clone invariant depends on exact counts)."""
        sid = getattr(sb, "id", None)
        if sid is None:
            return None
        payload = None
        with self._lock:
            existing = self._records.get(sid)
            if existing is not None:
                # Already known — refresh the live handle/destroyer, no re-count.
                self._handles[sid] = sb
                if destroy_fn is not None:
                    self._destroyers[sid] = destroy_fn
                return None
            now = self._now()
            rec = {
                "id": sid,
                "role": role,
                "backend": backend,
                "state": state,
                "parent_id": parent_id,
                "created_ts": now,
                "updated_ts": now,
                "labels": dict(labels) if labels else {},
                "isolation": isolation,
                "current_cmd": None,
                "exec_count": 0,
                "recent_execs": collections.deque(maxlen=self._exec_history),
                "preview_url": None,
            }
            self._records[sid] = rec
            self._handles[sid] = sb
            if destroy_fn is not None:
                self._destroyers[sid] = destroy_fn
            self._total_ever += 1
            if parent_id is not None:
                kids = self._children.setdefault(parent_id, [])
                if sid not in kids:
                    kids.append(sid)
            payload = self._registered_payload(rec)
        self._emit("sandbox_registered", payload)
        return None

    @_safe
    def set_state(self, sid, state, current_cmd=None):
        """Update a sandbox's lifecycle state (+ updated_ts) and emit
        `sandbox_state`. No-op on an unknown id."""
        payload = None
        with self._lock:
            rec = self._records.get(sid)
            if rec is None:
                return None
            rec["state"] = state
            rec["current_cmd"] = current_cmd
            rec["updated_ts"] = self._now()
            payload = {"id": sid, "state": state, "current_cmd": current_cmd}
        self._emit("sandbox_state", payload)
        return None

    @_safe
    def exec_started(self, sid, cmd, isolation=None):
        """A command started on this sandbox: state -> running-cmd, remember the
        (truncated) command, and record the flake-class isolation if given.
        Emits `sandbox_state`. No-op on an unknown id."""
        payload = None
        with self._lock:
            rec = self._records.get(sid)
            if rec is None:
                return None
            cur = cmd[:160] if cmd else None
            rec["state"] = "running-cmd"
            rec["current_cmd"] = cur
            if isolation is not None:
                rec["isolation"] = isolation
            rec["updated_ts"] = self._now()
            payload = {"id": sid, "state": "running-cmd", "current_cmd": cur}
        self._emit("sandbox_state", payload)
        return None

    @_safe
    def exec_finished(self, sid, cmd, exit_code, output_tail, duration_s):
        """A command finished: append to the ring buffer, bump exec_count, state
        back to warm. Emits exactly ONE `sandbox_exec` — the exec event IS the
        running-cmd -> warm transition (no paired sandbox_state; event-volume
        discipline). No-op on an unknown id."""
        payload = None
        with self._lock:
            rec = self._records.get(sid)
            if rec is None:
                return None
            cmd_t = cmd[:160] if cmd else ""
            tail = str(output_tail or "")[-200:]
            now = self._now()
            rec["recent_execs"].append({
                "cmd": cmd_t,
                "exit_code": exit_code,
                "output_tail": tail,
                "duration_s": duration_s,
                "ts": now,
            })
            rec["exec_count"] += 1
            rec["state"] = "warm"
            rec["current_cmd"] = None
            rec["updated_ts"] = now
            payload = {
                "id": sid,
                "cmd": cmd_t,
                "exit_code": exit_code,
                "duration_s": duration_s,
                "output_tail": tail,
                "exec_count": rec["exec_count"],
            }
        self._emit("sandbox_exec", payload)
        return None

    @_safe
    def mark_destroyed(self, sid):
        """Mark a sandbox destroyed, drop its handle/destroyer, prune past the
        retention window, and emit `sandbox_destroyed`. Idempotent: a second
        call is a no-op (no double count, no double retention entry)."""
        payload = None
        with self._lock:
            rec = self._records.get(sid)
            if rec is None or rec["state"] == "destroyed":
                return None
            rec["state"] = "destroyed"
            rec["current_cmd"] = None
            rec["updated_ts"] = self._now()
            role = rec["role"]
            self._destroyed += 1
            self._handles.pop(sid, None)
            self._destroyers.pop(sid, None)
            self._destroyed_order.append(sid)
            self._prune_destroyed_locked()
            payload = {"id": sid, "role": role}
        self._emit("sandbox_destroyed", payload)
        return None

    @_safe
    def mark_degraded(self, sid):
        """Mark a fork root/checkpoint degraded (the pool degraded under it) so
        the tree shows the dead fork spine honestly. Emits `sandbox_state`."""
        payload = None
        with self._lock:
            rec = self._records.get(sid)
            if rec is None:
                return None
            rec["state"] = "degraded"
            rec["current_cmd"] = None
            rec["updated_ts"] = self._now()
            payload = {"id": sid, "state": "degraded", "current_cmd": None}
        self._emit("sandbox_state", payload)
        return None

    @_safe
    def preview(self, sid, port=None):
        """Lazily resolve the Daytona preview URL for a sandbox.

        Caching rule (see OBSERVATORY-PLAN.md): positive results cache forever;
        negative results are NEVER cached in a non-terminal state. Roots and
        checkpoints spend almost their whole life paused, and a paused sandbox
        typically cannot produce a preview link — so a failure returns None
        WITHOUT caching, and the next drawer-open retries (and may catch the
        sandbox awake). A destroyed sandbox has no handle, so it simply returns
        None. Only ever called from GET /sandboxes/{id}, never on a hot path."""
        with self._lock:
            rec = self._records.get(sid)
            if rec is None:
                return None
            if rec["preview_url"] is not None:
                return rec["preview_url"]          # positive cache, forever
            handle = self._handles.get(sid)
        if handle is None:
            return None                            # destroyed / no handle
        if port is None:
            port = int(os.environ.get("RETRIAL_PREVIEW_PORT", "8080"))
        try:
            link = handle.get_preview_link(port)
        except Exception:
            return None                            # negative: NOT cached here
        url = getattr(link, "url", None)
        if url is None and isinstance(link, str):
            url = link
        if url is None:
            return None
        with self._lock:
            rec = self._records.get(sid)
            if rec is not None:
                rec["preview_url"] = url
        return url

    # -- destruction routing --------------------------------------------
    @_safe
    def destroy(self, sid):
        """Initiate destruction of ONE sandbox through its owner.

        Routes to the owner's destroy_fn(sid) if present (so pool bookkeeping
        can never lease a destroyed sandbox), else best-effort handle.delete().
        Returns True if a destruction was initiated. Must NOT hold self._lock
        while calling out — snapshot the destroyer, release, then call."""
        with self._lock:
            fn = self._destroyers.get(sid)
            handle = self._handles.get(sid)
        if fn is not None:
            fn(sid)
            return True
        if handle is not None and hasattr(handle, "delete"):
            try:
                handle.delete()
            except Exception:
                pass
            return True
        return False

    @_safe
    def reap_orphans(self):
        """Leaf-first destruction of every still-live sandbox that has a handle.

        The global-teardown safety net for records whose owning pool object is
        already gone: sort by lineage depth descending (children before
        parents) so Daytona never refuses a parent-delete while a child lives.
        Returns the count for which a destruction was initiated."""
        with self._lock:
            candidates = [(sid, self._depth_locked(sid))
                          for sid, rec in self._records.items()
                          if rec["state"] != "destroyed" and sid in self._handles]
        candidates.sort(key=lambda x: x[1], reverse=True)  # deepest first
        count = 0
        for sid, _ in candidates:
            if self.destroy(sid):
                count += 1
        return count

    @_safe
    def emit_snapshot(self):
        """Broadcast the full current sandbox world as `registry_snapshot`.

        Called from _accept_run() right after BUS.reset() to re-seed the fresh
        replay buffer (the stale-bleed cure), and from GET /sandboxes on demand
        is NOT needed (that endpoint returns snapshot() directly)."""
        self._emit("registry_snapshot", self.snapshot())
        return None

    # -- pure readers (hold the lock, never raise by construction) -------
    def snapshot(self):
        """{"sandboxes": [record-minus-recent_execs], "counts": {...},
        "lineage": {parent: [children]}}. `sandboxes` is bounded to live +
        <= destroyed_retain destroyed records; counts come from the exact int
        counters, never from len(sandboxes)."""
        with self._lock:
            sandboxes = [self._snapshot_record(r) for r in self._records.values()]
            lineage = {p: list(kids) for p, kids in self._children.items() if kids}
            counts = {
                "live": self._total_ever - self._destroyed,
                "total_ever": self._total_ever,
                "destroyed": self._destroyed,
            }
        return {"sandboxes": sandboxes, "counts": counts, "lineage": lineage}

    def record(self, sid):
        """The FULL record for one sandbox incl. recent_execs (list-ified), or
        None (unknown or pruned)."""
        with self._lock:
            rec = self._records.get(sid)
            if rec is None:
                return None
            out = dict(rec)
            out["recent_execs"] = list(rec["recent_execs"])
            out["labels"] = dict(rec["labels"])
            return out

    def counts(self):
        """{"live", "total_ever", "destroyed"} from the exact int counters."""
        with self._lock:
            return {
                "live": self._total_ever - self._destroyed,
                "total_ever": self._total_ever,
                "destroyed": self._destroyed,
            }

    def lineage(self):
        """{parent_id: [child_ids]} — the fork-lineage index (copy)."""
        with self._lock:
            return {p: list(kids) for p, kids in self._children.items() if kids}


# The process-wide default registry. The server attaches the process BUS to it
# at import; the pools/bisector/trial runner feed it via their `registry=`
# kwarg (defaulting to this). Tests build their own private SandboxRegistry.
REGISTRY = SandboxRegistry()
