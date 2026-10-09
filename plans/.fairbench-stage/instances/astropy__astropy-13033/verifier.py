# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
Verifier for: "TimeSeries: misleading exception when required column check
fails".

Every expected message is built by the helpers below, which encode the rules
of the "Required behaviour" section:

* missing form:  "{cls} object is invalid - missing required column{s} {missing}"
* order form:    "{cls} object is invalid - required column{s} {required}
                  must come first, but found {found}"
* the missing form wins whenever any required column is absent;
* {missing} lists absent required columns in required order;
* {found} is the names in the leading len(required) positions;
* {s} is "s" iff the clause names more than one column;
* one name renders as 'name', several as a Python list.
"""

import numpy as np
import pytest

from astropy import units as u
from astropy.table import Table, Column, vstack
from astropy.time import Time

from astropy.timeseries.sampled import TimeSeries
from astropy.timeseries.binned import BinnedTimeSeries

INPUT_TIME = Time(['2016-03-22T12:30:31',
                   '2015-01-21T12:30:32',
                   '2016-03-22T12:30:40'])
PLAIN_TABLE = Table([[1, 2, 11], [3, 4, 1], [1, 1, 1]], names=['a', 'b', 'c'])

SAMPLED_REQUIRED = ['time']
BINNED_REQUIRED = ['time_bin_start', 'time_bin_size']


# --------------------------------------------------------------------------
# The rules of the requirement
# --------------------------------------------------------------------------

def render(names):
    """One name renders in single quotes; several render as a Python list."""
    names = list(names)
    if len(names) == 1:
        return f"'{names[0]}'"
    return str(names)


def plural(names):
    """'s' when the clause names more than one column, otherwise empty."""
    return 's' if len(names) > 1 else ''


def missing_message(cls_name, missing):
    return (f"{cls_name} object is invalid - "
            f"missing required column{plural(missing)} {render(missing)}")


def order_message(cls_name, required, found):
    return (f"{cls_name} object is invalid - "
            f"required column{plural(required)} {render(required)} "
            f"must come first, but found {render(found)}")


def expected_message(cls_name, required, colnames, form):
    """
    Apply the rule to the column layout the object has when it is checked.

    ``form`` names the branch the scenario is meant to exercise, so a
    mis-specified scenario cannot silently test the other form.
    """
    required = list(required)
    colnames = list(colnames)
    missing = [name for name in required if name not in colnames]
    if missing:
        assert form == 'missing', 'scenario error: a required column is absent'
        return missing_message(cls_name, missing)
    found = colnames[:len(required)]
    assert found != required, 'scenario error: object would be valid'
    assert form == 'order', 'scenario error: every required column is present'
    return order_message(cls_name, required, found)


def assert_check_fails(ts, operation, required, colnames_after, form):
    """Run ``operation`` and compare the ValueError text with the rule."""
    cls_name = type(ts).__name__
    with pytest.raises(ValueError) as exc:
        operation(ts)
    assert exc.value.args[0] == expected_message(cls_name, required,
                                                 colnames_after, form)


# --------------------------------------------------------------------------
# Object factories (idioms copied from the existing timeseries tests)
# --------------------------------------------------------------------------

def make_sampled(names=('a', 'b'), cls=TimeSeries):
    data = [[10, 2, 3], [4, 5, 6], [7, 8, 9]][:len(names)]
    return cls(time=INPUT_TIME, data=data, names=list(names))


def make_binned(cls=BinnedTimeSeries):
    # colnames: ['time_bin_start', 'time_bin_size', 'a', 'b', 'c']
    return cls(time_bin_start=INPUT_TIME, time_bin_size=3 * u.s,
               data=PLAIN_TABLE)


def new_col(name):
    return Column([3, 4, 5], name=name)


class MyTimeSeries(TimeSeries):
    pass


class MyBinnedTimeSeries(BinnedTimeSeries):
    pass


# --------------------------------------------------------------------------
# The example from the issue
# --------------------------------------------------------------------------

def test_issue_example():
    time = Time(np.arange(100000, 100003), format='jd')
    ts = TimeSeries(time=time, data={"flux": [99.9, 99.8, 99.7]})
    ts._required_columns = ["time", "flux"]
    # 'flux' alone is absent; two columns are required but the clause names
    # one, so the message is singular.
    assert_check_fails(ts, lambda t: t.remove_column("flux"),
                       ["time", "flux"], ['time'], 'missing')


# --------------------------------------------------------------------------
# TimeSeries with its default required column ['time'] (replaces the old
# assertions in test_sampled.py::test_required_columns)
# --------------------------------------------------------------------------

SAMPLED_DEFAULT_CASES = [
    # id, operation, column layout after the operation, form
    ('add_column_at_front',
     lambda ts: ts.add_column(new_col('c'), index=0),
     ['c', 'time', 'a', 'b'], 'order'),
    ('add_columns_at_front',
     lambda ts: ts.add_columns([new_col('d'), new_col('e')], indexes=[0, 1]),
     ['d', 'time', 'e', 'a', 'b'], 'order'),
    ('keep_columns',
     lambda ts: ts.keep_columns(['a', 'b']),
     ['a', 'b'], 'missing'),
    ('remove_column',
     lambda ts: ts.remove_column('time'),
     ['a', 'b'], 'missing'),
    ('remove_columns',
     lambda ts: ts.remove_columns(['time', 'a']),
     ['b'], 'missing'),
    ('rename_column',
     lambda ts: ts.rename_column('time', 'banana'),
     ['banana', 'a', 'b'], 'missing'),
    ('remove_every_column',
     lambda ts: ts.remove_columns(['time', 'a', 'b']),
     [], 'missing'),
]


@pytest.mark.parametrize('operation, after, form',
                         [case[1:] for case in SAMPLED_DEFAULT_CASES],
                         ids=[case[0] for case in SAMPLED_DEFAULT_CASES])
def test_sampled_default_required(operation, after, form):
    ts = make_sampled()
    assert_check_fails(ts, operation, SAMPLED_REQUIRED, after, form)


# --------------------------------------------------------------------------
# TimeSeries with _required_columns overridden on the object
# --------------------------------------------------------------------------

CUSTOM_CASES = [
    # id, data column names, required, operation, layout after, form
    ('remove_one_of_two_required',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.remove_column('a'),
     ['time', 'b'], 'missing'),
    ('rename_one_of_two_required',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.rename_column('a', 'alpha'),
     ['time', 'alpha', 'b'], 'missing'),
    ('keep_columns_drops_one_required',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.keep_columns(['time', 'b']),
     ['time', 'b'], 'missing'),
    ('remove_both_required_in_reverse_order',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.remove_columns(['a', 'time']),
     ['b'], 'missing'),
    ('remove_every_column',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.remove_columns(['time', 'a', 'b']),
     [], 'missing'),
    ('missing_listed_in_required_order',
     ('c', 'a', 'b'), ['time', 'c', 'a'],
     lambda ts: ts.remove_columns(['a', 'c']),
     ['time', 'b'], 'missing'),
    ('three_missing_listed_in_required_order',
     ('c', 'a', 'b'), ['time', 'c', 'a'],
     lambda ts: ts.remove_columns(['a', 'time', 'c']),
     ['b'], 'missing'),
    ('missing_wins_over_misplaced',
     ('a', 'b'), ['time', 'flux'],
     lambda ts: ts.add_column(new_col('c'), index=0),
     ['c', 'time', 'a', 'b'], 'missing'),
    ('insert_between_two_required',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.add_column(new_col('c'), index=1),
     ['time', 'c', 'a', 'b'], 'order'),
    ('insert_before_two_required',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.add_column(new_col('c'), index=0),
     ['c', 'time', 'a', 'b'], 'order'),
    ('add_columns_before_two_required',
     ('a', 'b'), ['time', 'a'],
     lambda ts: ts.add_columns([new_col('x'), new_col('y')], indexes=[0, 3]),
     ['x', 'time', 'a', 'b', 'y'], 'order'),
    ('required_present_in_wrong_relative_order',
     ('a', 'b'), ['time', 'b', 'a'],
     lambda ts: ts.add_column(new_col('z')),
     ['time', 'a', 'b', 'z'], 'order'),
    ('insert_among_three_required',
     ('c', 'a', 'b'), ['time', 'c', 'a'],
     lambda ts: ts.add_column(new_col('z'), index=2),
     ['time', 'c', 'z', 'a', 'b'], 'order'),
]


@pytest.mark.parametrize('names, required, operation, after, form',
                         [case[1:] for case in CUSTOM_CASES],
                         ids=[case[0] for case in CUSTOM_CASES])
def test_sampled_custom_required(names, required, operation, after, form):
    ts = make_sampled(names)
    ts._required_columns = list(required)
    assert_check_fails(ts, operation, required, after, form)


# --------------------------------------------------------------------------
# BinnedTimeSeries shares the base class, so the same rules apply
# --------------------------------------------------------------------------

BINNED_CASES = [
    # id, operation, layout after, form
    ('remove_time_bin_size',
     lambda ts: ts.remove_column('time_bin_size'),
     ['time_bin_start', 'a', 'b', 'c'], 'missing'),
    ('remove_time_bin_start',
     lambda ts: ts.remove_column('time_bin_start'),
     ['time_bin_size', 'a', 'b', 'c'], 'missing'),
    ('remove_both_required_in_reverse_order',
     lambda ts: ts.remove_columns(['time_bin_size', 'time_bin_start']),
     ['a', 'b', 'c'], 'missing'),
    ('remove_every_column',
     lambda ts: ts.remove_columns(['time_bin_start', 'time_bin_size',
                                   'a', 'b', 'c']),
     [], 'missing'),
    ('rename_time_bin_size',
     lambda ts: ts.rename_column('time_bin_size', 'size'),
     ['time_bin_start', 'size', 'a', 'b', 'c'], 'missing'),
    ('keep_columns',
     lambda ts: ts.keep_columns(['time_bin_start', 'a']),
     ['time_bin_start', 'a'], 'missing'),
    ('insert_between_required',
     lambda ts: ts.add_column(new_col('x'), index=1),
     ['time_bin_start', 'x', 'time_bin_size', 'a', 'b', 'c'], 'order'),
    ('insert_before_required',
     lambda ts: ts.add_column(new_col('x'), index=0),
     ['x', 'time_bin_start', 'time_bin_size', 'a', 'b', 'c'], 'order'),
]


@pytest.mark.parametrize('operation, after, form',
                         [case[1:] for case in BINNED_CASES],
                         ids=[case[0] for case in BINNED_CASES])
def test_binned_required(operation, after, form):
    ts = make_binned()
    assert_check_fails(ts, operation, BINNED_REQUIRED, after, form)


# --------------------------------------------------------------------------
# Objects created empty, where columns are added one at a time. While being
# built, only the first len(colnames) required columns are required.
# --------------------------------------------------------------------------

def effective_required(required, colnames):
    return list(required)[:len(colnames)]


def test_sampled_empty_initialization_invalid():
    # Replaces test_sampled.py::test_empty_initialization_invalid
    ts = TimeSeries()
    after = ['flux']
    assert_check_fails(ts, lambda t: t.__setitem__('flux', [1, 2, 3]),
                       effective_required(SAMPLED_REQUIRED, after), after,
                       'missing')


def test_binned_empty_initialization_invalid():
    # Replaces test_binned.py::test_empty_initialization_invalid.
    # Judgement call: with one column present only 'time_bin_start' is
    # required yet, so only it is reported missing (singular).
    ts = BinnedTimeSeries()
    after = ['flux']
    assert_check_fails(ts, lambda t: t.__setitem__('flux', [1, 2, 3]),
                       effective_required(BINNED_REQUIRED, after), after,
                       'missing')


def test_binned_partial_initialization_missing_second_required():
    ts = BinnedTimeSeries()
    ts['time_bin_start'] = Time([1, 2, 3], format='mjd')
    after = ['time_bin_start', 'flux']
    assert_check_fails(ts, lambda t: t.__setitem__('flux', [1, 2, 3]),
                       effective_required(BINNED_REQUIRED, after), after,
                       'missing')


def test_binned_partial_initialization_required_in_wrong_order():
    ts = BinnedTimeSeries()
    ts['time_bin_start'] = Time([1, 2, 3], format='mjd')
    after = ['time_bin_size', 'time_bin_start']
    assert_check_fails(
        ts, lambda t: t.add_column(Column([1, 2, 3], name='time_bin_size'),
                                   index=0),
        effective_required(BINNED_REQUIRED, after), after, 'order')


# --------------------------------------------------------------------------
# An object with no columns at all takes the missing form, listing every
# required column (reached here through stacking, as in test_common.py)
# --------------------------------------------------------------------------

@pytest.mark.parametrize('make, required', [
    (lambda: TimeSeries(time=INPUT_TIME, data=PLAIN_TABLE), SAMPLED_REQUIRED),
    (lambda: BinnedTimeSeries(time_bin_start=INPUT_TIME,
                              time_bin_size=3 * u.s, data=PLAIN_TABLE),
     BINNED_REQUIRED),
], ids=['TimeSeries', 'BinnedTimeSeries'])
def test_no_columns_after_stacking(make, required):
    series = make()
    ts = vstack([series, series])
    colnames = list(ts.colnames)
    assert_check_fails(ts, lambda t: t.remove_columns(colnames),
                       required, [], 'missing')


# --------------------------------------------------------------------------
# {cls} is the class name of the object being checked
# --------------------------------------------------------------------------

def test_cls_is_subclass_name_missing_form():
    ts = make_sampled(cls=MyTimeSeries)
    assert_check_fails(ts, lambda t: t.remove_column('time'),
                       SAMPLED_REQUIRED, ['a', 'b'], 'missing')
    assert type(ts).__name__ == 'MyTimeSeries'


def test_cls_is_subclass_name_order_form():
    ts = make_binned(cls=MyBinnedTimeSeries)
    assert_check_fails(ts, lambda t: t.add_column(new_col('x'), index=0),
                       BINNED_REQUIRED,
                       ['x', 'time_bin_start', 'time_bin_size', 'a', 'b', 'c'],
                       'order')
    assert type(ts).__name__ == 'MyBinnedTimeSeries'


def test_cls_is_subclass_name_empty_initialization():
    ts = MyTimeSeries()
    after = ['flux']
    assert_check_fails(ts, lambda t: t.__setitem__('flux', [1, 2, 3]),
                       effective_required(SAMPLED_REQUIRED, after), after,
                       'missing')


# --------------------------------------------------------------------------
# The deferred check (run when _delay_required_column_checks exits) uses
# the same messages
# --------------------------------------------------------------------------

def test_delayed_check_uses_new_message():
    ts = make_sampled()
    ts._required_columns = ['time', 'a']

    def operation(t):
        with t._delay_required_column_checks():
            t.remove_column('a')
            t.add_column(new_col('c'), index=0)

    assert_check_fails(ts, operation, ['time', 'a'], ['c', 'time', 'b'],
                       'missing')


# --------------------------------------------------------------------------
# Valid objects stay valid (existing behaviour that must keep passing)
# --------------------------------------------------------------------------

def test_valid_operations_do_not_raise():
    ts = TimeSeries()
    ts['time'] = Time([50001, 50002, 50003], format='mjd')
    ts['flux'] = [1, 2, 3]

    bts = BinnedTimeSeries()
    bts['time_bin_start'] = Time([1, 2, 3], format='mjd')
    bts['time_bin_size'] = [1, 2, 3] * u.s
    bts['flux'] = [1, 2, 3]

    custom = make_sampled()
    custom._required_columns = ['time', 'a']
    custom.remove_column('b')
    custom.add_column(new_col('z'))
    assert custom.colnames == ['time', 'a', 'z']

    binned = make_binned()
    binned.remove_column('c')
    binned.add_column(new_col('x'), index=2)
