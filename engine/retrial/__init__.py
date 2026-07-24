"""Retrial engine — empirical flake-rate detection and a fix tournament on a
swarm of disposable Daytona sandboxes.

Public API:
    SandboxPool         warm/lease/release/destroy_all pool of fresh sandboxes
    ForkSandboxPool     Rewind fork engine: byte-identical clones of one warm
                        checkpoint, sticky fallback to SandboxPool
    make_pool           backend factory (env RETRIAL_POOL_BACKEND=fork|snapshot)
    run_trial, TrialRunner   one test execution in one fresh sandbox
    verify, confirm, Verifier, wilson   flake rate + Wilson CI + adaptive stop
    EventBus            thread-safe typed-event fan-out with replay buffer
    TournamentCoordinator    detect -> verify-per-hypothesis -> confirm DAG
    FlakeBisector       time-travel bisection: checkpoint the suite at test
                        boundaries, binary-search to the polluting test
    EvidenceLedger      Braintrust experiments + real permalinks (audit trail)
    DiagnosisEngine, diagnose   Fireworks differential-diagnosis hypotheses
    SandboxRegistry     thread-safe ledger of every sandbox — the Observatory feed
"""
from .pool import SandboxPool, make_pool
from .forkpool import ForkSandboxPool
from .trial import run_trial, TrialRunner
from .verifier import verify, confirm, wilson, Verifier, verify_hermetic
from .events import EventBus, EVENT_TYPES
from .coordinator import TournamentCoordinator
from .bisect import FlakeBisector
from .ledger import EvidenceLedger, LedgerRun
from .diagnosis import DiagnosisEngine, diagnose
from .genome import Genome
from .prsmith import PRSmith
from .registry import SandboxRegistry, REGISTRY

__all__ = [
    "SandboxPool",
    "ForkSandboxPool",
    "make_pool",
    "run_trial",
    "TrialRunner",
    "verify",
    "confirm",
    "verify_hermetic",
    "wilson",
    "Verifier",
    "EventBus",
    "EVENT_TYPES",
    "TournamentCoordinator",
    "FlakeBisector",
    "EvidenceLedger",
    "LedgerRun",
    "DiagnosisEngine",
    "diagnose",
    "Genome",
    "PRSmith",
    "SandboxRegistry",
    "REGISTRY",
]
