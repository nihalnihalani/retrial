"""Reproduction of a flaky penman test, extracted at v1.2.1.

Standalone extract of goodmami/penman tests/test_layout.py::test_rearrange.
Requires `pip install penman==1.2.1`. Exit 0 = pass, exit 1 = fail.
"""
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
