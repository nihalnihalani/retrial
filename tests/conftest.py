"""Shared test scaffolding: sys.path bootstrap + hand-rolled Daytona fakes.

No real Daytona API calls anywhere in the suite. The fakes model exactly the
SDK surface the pools touch (create/get/delete, process.exec, and the
experimental fork/pause/start trio), in the scripted style ported from the
Rewind unit tests: a checkpoint's fork behavior is a consumable list of
("child", id) | ("raise",) specs — an exhausted script keeps failing, which
models a fork that also fails on every `_retry` attempt, not just the first.

    .venv/bin/python -m pytest tests/ -q
"""
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))  # so `import retrial` works from repo root


# ----------------------------- fakes -----------------------------
class FakeExecResult:
    def __init__(self, result="", exit_code=0):
        self.result = result
        self.exit_code = exit_code


class FakeProcess:
    def __init__(self, result=""):
        self.execs = []  # (cmd, timeout)
        # Scripted output string returned by every exec (default ""). Set e.g.
        # "EXIT:0\n" so the bisector/trial parser reads a real exit code.
        self.result = result

    def exec(self, cmd, timeout=None):
        self.execs.append((cmd, timeout))
        return FakeExecResult(result=self.result)


class FakePreviewLink:
    """Stands in for Daytona's preview-link object (has a `.url`)."""

    def __init__(self, url):
        self.url = url


class FakeChild:
    """A forked trial sandbox (leaf)."""

    def __init__(self, cid):
        self.id = cid
        self.deleted = False
        self.pause_calls = 0
        self.process = FakeProcess()

    def delete(self):
        self.deleted = True

    def pause(self):
        self.pause_calls += 1

    def start(self):
        pass

    def get_preview_link(self, port):
        return FakePreviewLink(f"https://preview.fake/{self.id}:{port}")


class FakeCheckpointSandbox:
    """Stands in for the checkpoint's sandbox handle (a paused fork of root).

    fork_results: None => every fork succeeds with a fresh unique child;
    otherwise a consumed-in-order script of ("child", id) | ("raise",).
    Thread-safe so the concurrency stress tests (SEAM-2) can reuse it: records
    in_flight / max_in_flight across concurrent _experimental_fork calls.
    """

    def __init__(self, cid="ckpt-1", fork_results=None, registry=None):
        self.id = cid
        self._fork_results = list(fork_results) if fork_results is not None else None
        self._auto_n = 0
        self.start_calls = 0
        self.pause_calls = 0
        self.fork_calls = 0
        self.children = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._mutex = threading.Lock()
        self._registry = registry if registry is not None else {}

    def start(self):
        self.start_calls += 1

    def pause(self):
        self.pause_calls += 1

    def get_preview_link(self, port):
        return FakePreviewLink(f"https://preview.fake/{self.id}:{port}")

    def _experimental_fork(self, name=None):
        with self._mutex:
            self.fork_calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            if self._fork_results is None:
                self._auto_n += 1
                spec = ("child", f"{self.id}-clone-{self._auto_n}")
            elif not self._fork_results:
                spec = ("raise",)  # exhausted script keeps failing
            else:
                spec = self._fork_results.pop(0)
        try:
            if spec[0] == "raise":
                raise RuntimeError("fork blew up")
            child = FakeChild(spec[1])
            with self._mutex:
                self.children.append(child)
                self._registry[child.id] = child
            return child
        finally:
            with self._mutex:
                self.in_flight -= 1


class FakeRootSandbox:
    """The one warm root sandbox: exec-able, forkable into a checkpoint."""

    def __init__(self, rid, ckpt_factory):
        self.id = rid
        self.process = FakeProcess()
        self.fork_calls = 0
        self._ckpt_factory = ckpt_factory

    def _experimental_fork(self, name=None):
        self.fork_calls += 1
        return self._ckpt_factory(name)

    def get_preview_link(self, port):
        return FakePreviewLink(f"https://preview.fake/{self.id}:{port}")


class FakeClient:
    """Mocked Daytona client: create/get/delete, plus scripted fork behavior.

    clone_script  -> fork_results handed to each checkpoint it spawns
                     (None = every clone fork succeeds).
    root_fork_fails=True -> the root's fork (checkpoint creation) raises,
                     modeling an SDK without the experimental primitive.
    `deleted` records ids in client-deletion order — the destroy_all
    order assertions read it.
    """

    def __init__(self, clone_script=None, root_fork_fails=False):
        self.create_calls = 0
        self.deleted = []
        self.registry = {}
        self.roots = []
        self.checkpoints = []
        self._clone_script = clone_script
        self._root_fork_fails = root_fork_fails
        self._mutex = threading.Lock()

    def create(self, params, timeout=None):
        with self._mutex:
            self.create_calls += 1
            rid = f"root-{self.create_calls}"

        def ckpt_factory(name):
            if self._root_fork_fails:
                raise AttributeError("'Sandbox' object has no attribute '_experimental_fork'")
            with self._mutex:
                ck = FakeCheckpointSandbox(
                    cid=f"ckpt-{len(self.checkpoints) + 1}",
                    fork_results=self._clone_script,
                    registry=self.registry)
                self.checkpoints.append(ck)
                self.registry[ck.id] = ck
            return ck

        sb = FakeRootSandbox(rid, ckpt_factory)
        with self._mutex:
            self.roots.append(sb)
            self.registry[sb.id] = sb
        return sb

    def get(self, sid):
        with self._mutex:
            return self.registry.get(sid) or FakeChild(sid)

    def delete(self, sb):
        sid = getattr(sb, "id", str(sb))
        with self._mutex:
            self.deleted.append(sid)
            obj = self.registry.pop(sid, None)
        if obj is not None:
            obj.deleted = True


class RaisingRegistry:
    """A SandboxRegistry look-alike whose EVERY public method raises.

    Injected into the pools/bisector to prove the never-break-a-run contract:
    with observability wired to explode, warm/lease/release/destroy_all must
    still behave byte-identically to a run with no registry at all. (The real
    registry swallows internally via @_safe; this fake goes further by raising
    from the call itself, exercising the call sites' own robustness.)
    """

    def _boom(self, *a, **k):
        raise RuntimeError("registry exploded (never-break-a-run probe)")

    attach_bus = register = set_state = exec_started = exec_finished = _boom
    mark_destroyed = mark_degraded = preview = destroy = reap_orphans = _boom
    emit_snapshot = snapshot = record = counts = lineage = _boom


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """Zero out _retry's backoff sleeps so induced-failure tests stay fast.

    Patches a stand-in `time` module onto retrial.forkpool only — the global
    time module (and any other module's sleeps) stay real.
    """
    import time as _time
    from types import SimpleNamespace
    from retrial import forkpool as forkpool_mod
    monkeypatch.setattr(forkpool_mod, "time",
                        SimpleNamespace(sleep=lambda s: None,
                                        monotonic=_time.monotonic))
