"""Trigger expression parser tests for the Ghost-Watcher daemon.

We don't test the polling loop here (network-dependent); the parser is
the safety-critical surface — a malformed trigger that silently parses
to something the user didn't intend is a foot-gun.
"""
from __future__ import annotations

import operator

import pytest

from gammaqc_terminal.watch import TriggerSpec


@pytest.mark.parametrize("expr,metric,op_sym,threshold", [
    ("price > 100",            "price",                ">",   100.0),
    ("price < 50.5",           "price",                "<",   50.5),
    ("pct_change >= -3.0",     "pct_change",           ">=",  -3.0),
    ("pct_change <= 2",        "pct_change",           "<=",  2.0),
    ("volume_spike > 3.0",     "volume_spike",         ">",   3.0),
    ("options_volume_spike > 1.5", "options_volume_spike", ">", 1.5),
    ("PRICE > 100",            "price",                ">",   100.0),    # case-insensitive
    ("  price  >  100  ",      "price",                ">",   100.0),    # whitespace-tolerant
])
def test_trigger_parse_valid(expr, metric, op_sym, threshold):
    spec = TriggerSpec.parse(expr)
    assert spec.metric == metric
    assert spec.op_symbol == op_sym
    assert spec.threshold == threshold


@pytest.mark.parametrize("expr", [
    "",                              # empty
    "price",                         # missing op + value
    "price >",                       # missing value
    "> 100",                         # missing metric
    "foo > 100",                     # unknown metric
    "price gt 100",                  # word op, not symbol
    "price > abc",                   # non-numeric threshold
    "price > 100 AND volume > 1",    # multi-clause not supported in v0.1
])
def test_trigger_parse_invalid_raises(expr):
    with pytest.raises(ValueError, match="unparseable trigger"):
        TriggerSpec.parse(expr)


def test_op_executes_correctly():
    spec = TriggerSpec.parse("price > 100")
    assert spec.op is operator.gt
    assert spec.op(101, 100) is True
    assert spec.op(99, 100) is False


def test_str_round_trips_to_parseable():
    spec = TriggerSpec.parse("volume_spike >= 2.5")
    spec2 = TriggerSpec.parse(str(spec))
    assert spec2.metric == spec.metric
    assert spec2.op_symbol == spec.op_symbol
    assert spec2.threshold == spec.threshold
