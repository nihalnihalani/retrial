# Minimal standalone reproduction of goodmami/penman
# tests/test_layout.py::test_rearrange (IDoFT NOD, PR #102).
# Documented cause: module-level random.seed(1) was ineffective, so
# rearrange(t, model.random_order) -- which sorts roles by random.random()
# -- produced a NON-deterministic arrangement. The assertion only passes
# when the random permutation happens to match the expected output.
# Here we run that exact branch UNSEEDED (each fresh process = fresh
# random entropy), reproducing the flake. exit 0=pass, 1=fail.
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
