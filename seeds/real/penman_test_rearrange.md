# Real-world flake specimen — `penman::test_rearrange`

The **real patient**: a documented flaky test from a real OSS Python project that
reproducibly flakes inside Daytona container sandboxes. This is *evidence* for Q&A
("we found a real flake in the wild and reproduced it on the same substrate"),
NOT the timed-demo specimen. The calibrated demo seed remains
`seeds/test_dict_order.py` (51%, locked).

## Provenance

| Field | Value |
|---|---|
| Project | [goodmami/penman](https://github.com/goodmami/penman) — PENMAN notation for graphs (AMR), pure-Python, MIT |
| Version | `penman==1.2.1` (pip-installable, no compiled deps) |
| Test | `tests/test_layout.py::test_rearrange` |
| IDoFT row | [`py-data.csv`](https://github.com/TestingResearchIllinois/idoft/blob/main/py-data.csv), Category **NOD** (non-order-dependent), Status **Accepted** |
| SHA (detected) | `7770dfe14b3d0d197cedc6640f3ff7e3bd695726` |
| Fix PR | [goodmami/penman#102](https://github.com/goodmami/penman/pull/102) |

## Documented root cause (randomness class — NOD)

`Model.random_order` is a role sort-key that returns `random.random()`
(`penman/model.py:300`):

```python
def random_order(self, role: Role):
    """Role sorting key that randomizes the order."""
    return random.random()
```

`test_rearrange` calls `rearrange(t, model.random_order)` and then asserts the
serialized tree equals **one specific permutation**. That assertion is only stable
if the `random` stream is seeded to the exact sequence that produces that
arrangement. The maintainer's fix (PR #102) moved `random.seed(1)` from module
level into the test body, because — in their words — *"the globally defined
`random.seed(1)` seems not working well here … setting the random seed within the
test itself seems safer than doing it within the module."* When the module-level
seed was ineffective (consumed/reset before the test ran), the sort produced a
random permutation and the assertion failed intermittently.

This is the **"NOD with randomness"** class the Retrial substrate reproduces:
a fresh `python3` process = fresh `random` entropy = a different permutation each run.
(Contrast with thread/timing races, which we measured do **not** flake on this substrate.)

## Minimal standalone reproduction

Faithful to the real test's `random_order` branch, run **unseeded** — i.e. under the
exact condition the bug created (seed ineffective). Requires `pip install penman==1.2.1`.
Exit 0 = pass, 1 = fail.

```python
# penman test_layout.py::test_rearrange -- random_order branch, unseeded (the bug).
import sys
from penman.model import Model
from penman.codec import PENMANCodec
from penman.layout import rearrange

codec = PENMANCodec()
model = Model()

t = codec.parse('''
    (a / alpha
       :ARG0 (b / beta
                :ARG0 (g / gamma)
                :ARG1 (d / delta))
       :ARG0-of d
       :ARG1 (e / epsilon))''')

rearrange(t, model.random_order)
expected = (
    '(a / alpha\n'
    '   :ARG0-of d\n'
    '   :ARG1 (e / epsilon)\n'
    '   :ARG0 (b / beta\n'
    '            :ARG0 (g / gamma)\n'
    '            :ARG1 (d / delta)))')
sys.exit(0 if codec.format(t) == expected else 1)
```

## Measured Daytona flake rate

Calibrated on Daytona containers (`target="us"`), warm-pool with **process isolation**
(install `penman==1.2.1` once per sandbox, then run the repro as N fresh `python3`
processes — each process = fresh random entropy). Sound here because the flake is
per-process random state, not filesystem/port pollution.

| Metric | Value |
|---|---|
| Trials | 40 (8 sandboxes × 5) |
| Fails | 35 |
| **Flake rate** | **88%** |
| Wilson 95% CI | **[74%, 95%]** |
| Infra errors | 0 |
| Wallclock | 10.9 s (incl. 8× pip install) |
| Local cross-check | 37/40 = 92% fail (fresh-process, macOS Python 3.14) |

Both passes and failures occur (5/40 passed on Daytona) — genuinely
non-deterministic, not merely broken. The rate is **skewed high** because the test
asserts one exact permutation out of many; when the seed is ineffective the random
sort rarely matches. This is the authentic behavior of the real test's random branch
— the rate is measured, not tuned. (It is not in the 40–55% "ideal demo" band, which
is why the demo keeps the calibrated `test_dict_order.py`.)

## Takeaway for the pitch / Q&A

- **Yes, this reproduces on the real substrate.** A real, IDoFT-catalogued, maintainer-fixed
  Python flake (randomness class) flakes on Daytona containers at 88% (CI 74–95%), 0 infra errors.
- Confirms the substrate's reach: **randomness/hash-ordering flakes reproduce; thread/timing
  races do not** (measured 0/120 elsewhere in this repo). Retrial's detector targets exactly the
  class that reproduces.
- Reproduced via the same process-isolation model the engine uses (`isolation="process"`),
  with a one-time `pip install` per warm sandbox.

## Reproduce it yourself

```bash
# from retrial/ with .env populated:
SCR=$(mktemp -d)
cp seeds/real/... # (repro embedded above -> $SCR/repro.py)
# 8 sandboxes x 5 trials:
POOL=8 PER=5 SCR=$SCR .venv/bin/python <calibrate_penman.py>
```
Calibration harness used: `scratchpad/calibrate_penman.py` (adapted from
`scripts/calibrate_seeds.py` — adds one-time `pip install penman==1.2.1` per warm
sandbox and reuses each sandbox for `PER` fresh-process trials).
