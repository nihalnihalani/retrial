"""Retrial engine — empirical flake-rate detection and a fix tournament on a
swarm of disposable Daytona sandboxes.

Public API:
    SandboxPool         warm/lease/release/destroy_all pool of fresh sandboxes
    run_trial, TrialRunner   one test execution in one fresh sandbox
    verify, confirm, Verifier, wilson   flake rate + Wilson CI + adaptive stop
    EventBus            thread-safe typed-event fan-out with replay buffer
    TournamentCoordinator    detect -> verify-per-hypothesis -> confirm DAG
    EvidenceLedger      Braintrust experiments + real permalinks (audit trail)
    DiagnosisEngine, diagnose   Fireworks differential-diagnosis hypotheses
"""
from .pool import SandboxPool
from .trial import run_trial, TrialRunner
from .verifier import verify, confirm, wilson, Verifier
from .events import EventBus, EVENT_TYPES
from .coordinator import TournamentCoordinator
from .ledger import EvidenceLedger, LedgerRun
from .diagnosis import DiagnosisEngine, diagnose
from .genome import Genome
from .prsmith import PRSmith

__all__ = [
    "SandboxPool",
    "run_trial",
    "TrialRunner",
    "verify",
    "confirm",
    "wilson",
    "Verifier",
    "EventBus",
    "EVENT_TYPES",
    "TournamentCoordinator",
    "EvidenceLedger",
    "LedgerRun",
    "DiagnosisEngine",
    "diagnose",
    "Genome",
    "PRSmith",
]
