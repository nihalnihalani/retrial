"""Retrial engine — empirical flake-rate detection and a fix tournament on a
swarm of disposable Daytona sandboxes.

Public API:
    SandboxPool         warm/lease/release/destroy_all pool of fresh sandboxes
    run_trial, TrialRunner   one test execution in one fresh sandbox
    verify, confirm, Verifier, wilson   flake rate + Wilson CI + adaptive stop
    EventBus            thread-safe typed-event fan-out with replay buffer
    TournamentCoordinator    detect -> verify-per-hypothesis -> confirm DAG
"""
from .pool import SandboxPool
from .trial import run_trial, TrialRunner
from .verifier import verify, confirm, wilson, Verifier
from .events import EventBus, EVENT_TYPES
from .coordinator import TournamentCoordinator

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
]
