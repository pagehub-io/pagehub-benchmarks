# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
Verifier for: "TimeSeries: misleading exception when required column check fails."

The required behaviour, restated as the rules this file encodes:

A failing required-column check raises ``ValueError`` whose message says what is
wrong, in exactly one of two forms.

1. If *any* required column is absent from the object::

       {cls} object is invalid - missing required column{s} {missing}

   ``{missing}`` is the required column names absent from the object, in the
   order they are required.  An object with no columns at all has every
   required column absent, so it takes this form.

2. If every required column is present but they are not in the leading
   positions::

       {cls} object is invalid - required column{s} {required} must come first, but found {found}

   ``{required}`` is the required column names, ``{found}`` is the column names
   actually occupying those leading positions.

``{cls}`` is the class name of the object being checked (the base class is
shared, so this applies to ``BinnedTimeSeries`` too).  ``{s}`` is ``s`` when the
clause names more than one column and empty otherwise - it agrees with the names
in *that clause*, not with the number of required columns.  A single name renders
in single quotes (``'time'``); several names render as a Python list
(``['time', 'flux']``).

Every expected message below is built by ``missing_message`` /
``order_message``, which are those rules written out as code.
"""

import pytest

from astropy import units as u
from astropy.table import Column
from astropy.time import Time

from astropy.timeseries.sampled import TimeSeries
from astropy.timeseries.binned import BinnedTimeSeries

INPUT_TIME = Time(['2016-03-22T12:30:31',
                   '2015-01-21T12:30:32',
                   '2016-03-22T12:30:40'])


# ---------------------------------------------------------------------------
# The message rules, as code.  Every expectation in this file goes through them.
# ---------------------------------------------------------------------------

def render_names(names):
    """One name renders in single quotes; several render as a Python list."""
    names = list(names)
    if len(names) == 1:
        return "'{}'".format(names[0])
    return "[{}]".format(", ".join("'{}'".format(name) for name in names))


def s_for(names):
    """``s`` when the clause names more than one column, otherwise empty."""
    return 's' if len(list(names)) > 1 else ''


def missing_message(cls_name, missing):
    """Form used when any required column is absent from the object."""
    return ("{cls} object is invalid - missing required column{s} {missing}"
            .format(cls=cls_name, s=s_for(missing),
                    missing=render_names(missing)))


def order_message(cls_name, required, found):
    """Form used when every required column is present but not leading."""
    return ("{cls} object is invalid - required column{s} {required} "
            "must come first, but found {found}"
            .format(cls=cls_name, s=s_for(required),
                    required=render_names(required), found=render_names(found)))


# ---------------------------------------------------------------------------
# Object builders.  Columns are created exactly as the existing tests do, and
# ``_required_columns`` is set directly to vary what is required.
# ---------------------------------------------------------------------------

def make_series(data=None, names=None, required=None):
    """A TimeSeries whose columns are ['time'] + names (default ['a', 'b'])."""
    if data is None and names is None:
        data, names = [[10, 2, 3], [4, 5, 6]], ['a', 'b']
    ts = TimeSeries(time=INPUT_TIME, data=data, names=names)
    if required is not None:
        ts._required_columns = list(required)
    return ts


def make_binned(required=None):
    """A BinnedTimeSeries with columns ['time_bin_start', 'time_bin_size', 'a']."""
    bts = BinnedTimeSeries(time_bin_start=INPUT_TIME, time_bin_size=3 * u.s,
                           data=[[1, 2, 3]], names=['a'])
    if required is not None:
        bts._required_columns = list(required)
    return bts


# ---------------------------------------------------------------------------
# Sanity: the check must not fire while the requirement is satisfied.
# ---------------------------------------------------------------------------

def test_valid_operations_do_not_raise():
    ts = make_series()
    ts.remove_column('a')                               # not a required column
    ts.add_column(Column([3, 4, 5], name='c'))          # appended after 'time'
    ts.rename_column('b', 'bb')
    assert ts.colnames[0] == 'time'

    empty = TimeSeries()
    empty['time'] = Time([50001, 50002, 50003], format='mjd')


# ---------------------------------------------------------------------------
# Form 1: a required column is absent -> "missing required column(s) ..."
# ---------------------------------------------------------------------------

def test_removing_an_extra_required_column_reports_it_missing():
    # The reproducer from the issue: 'time' is still there, 'flux' is gone, so
    # the missing clause names one column even though two are required - {s}
    # agrees with the clause, not with the number required.
    ts = make_series(data=[[99.9, 99.8, 99.7]], names=['flux'],
                     required=['time', 'flux'])
    with pytest.raises(ValueError) as exc:
        ts.remove_column('flux')
    assert str(exc.value) == missing_message('TimeSeries', ['flux'])


def test_removing_the_time_column_reports_it_missing():
    ts = make_series()                                  # required: ['time']
    with pytest.raises(ValueError) as exc:
        ts.remove_column('time')
    assert str(exc.value) == missing_message('TimeSeries', ['time'])


def test_removing_several_columns_including_time_reports_time_missing():
    ts = make_series()
    with pytest.raises(ValueError) as exc:
        ts.remove_columns(['time', 'a'])
    assert str(exc.value) == missing_message('TimeSeries', ['time'])


def test_keeping_only_non_required_columns_reports_time_missing():
    ts = make_series()
    with pytest.raises(ValueError) as exc:
        ts.keep_columns(['a', 'b'])
    assert str(exc.value) == missing_message('TimeSeries', ['time'])


def test_renaming_the_time_column_reports_time_missing():
    # 'time' is no longer a column of the object, so it is missing; the new
    # name is not reported as a wrongly-ordered column.
    ts = make_series()
    with pytest.raises(ValueError) as exc:
        ts.rename_column('time', 'banana')
    assert str(exc.value) == missing_message('TimeSeries', ['time'])


def test_two_missing_columns_are_listed_in_the_order_they_are_required():
    # Required order is ['time', 'alpha']; sorting or using the removal order
    # would render ['alpha', 'time'] instead.
    ts = make_series(data=[[1, 2, 3], [4, 5, 6]], names=['alpha', 'b'],
                     required=['time', 'alpha'])
    with pytest.raises(ValueError) as exc:
        ts.remove_columns(['alpha', 'time'])
    assert str(exc.value) == missing_message('TimeSeries', ['time', 'alpha'])


def test_first_column_added_to_an_empty_series_reports_time_missing():
    # Supersedes test_sampled.py::test_empty_initialization_invalid.
    ts = TimeSeries()
    with pytest.raises(ValueError) as exc:
        ts['flux'] = [1, 2, 3]
    assert str(exc.value) == missing_message('TimeSeries', ['time'])


# ---------------------------------------------------------------------------
# Form 1, degenerate case: an object with no columns at all.
# ---------------------------------------------------------------------------

def test_object_with_no_columns_reports_the_single_required_column_missing():
    ts = make_series()
    with pytest.raises(ValueError) as exc:
        ts.remove_columns(['time', 'a', 'b'])
    assert ts.colnames == []
    assert str(exc.value) == missing_message('TimeSeries', ['time'])


def test_object_with_no_columns_reports_all_required_columns_missing():
    ts = make_series(data=[[1, 2, 3]], names=['alpha'],
                     required=['time', 'alpha'])
    with pytest.raises(ValueError) as exc:
        ts.remove_columns(['time', 'alpha'])
    assert ts.colnames == []
    assert str(exc.value) == missing_message('TimeSeries', ['time', 'alpha'])


# ---------------------------------------------------------------------------
# Form 2: every required column is present, but not in the leading positions.
# ---------------------------------------------------------------------------

def test_column_inserted_before_time_reports_ordering():
    ts = make_series()                                  # required: ['time']
    with pytest.raises(ValueError) as exc:
        ts.add_column(Column([3, 4, 5], name='c'), index=0)
    # 'time' is still a column, so nothing is missing; the one leading
    # position now holds 'c'.
    assert str(exc.value) == order_message('TimeSeries', ['time'], ['c'])


def test_columns_inserted_before_time_report_only_the_leading_positions():
    ts = make_series()
    with pytest.raises(ValueError) as exc:
        ts.add_columns([Column([3, 4, 5], name='d'),
                        Column([3, 4, 5], name='e')], indexes=[0, 1])
    # One column is required, so exactly one leading position is reported.
    assert str(exc.value) == order_message('TimeSeries', ['time'], ['d'])


def test_two_required_columns_out_of_position_report_both_and_what_was_found():
    ts = make_series(data=[[99.9, 99.8, 99.7]], names=['flux'],
                     required=['time', 'flux'])
    with pytest.raises(ValueError) as exc:
        ts.add_column(Column([3, 4, 5], name='x'), index=0)
    # Columns are now ['x', 'time', 'flux']: both required columns are present,
    # and the two leading positions hold ['x', 'time'].
    assert str(exc.value) == order_message('TimeSeries',
                                           ['time', 'flux'], ['x', 'time'])


# ---------------------------------------------------------------------------
# The base class is shared: BinnedTimeSeries gets the same two forms, with its
# own class name and its own two required columns.
# ---------------------------------------------------------------------------

def test_binned_missing_one_of_two_required_columns():
    bts = make_binned()     # required: ['time_bin_start', 'time_bin_size']
    with pytest.raises(ValueError) as exc:
        bts.remove_column('time_bin_size')
    assert str(exc.value) == missing_message('BinnedTimeSeries',
                                             ['time_bin_size'])


def test_binned_missing_both_required_columns():
    bts = make_binned()
    with pytest.raises(ValueError) as exc:
        bts.remove_columns(['time_bin_start', 'time_bin_size'])
    assert str(exc.value) == missing_message(
        'BinnedTimeSeries', ['time_bin_start', 'time_bin_size'])


def test_binned_required_columns_present_but_not_leading():
    bts = make_binned()
    with pytest.raises(ValueError) as exc:
        bts.add_column(Column([3, 4, 5], name='x'), index=0)
    # Columns are now ['x', 'time_bin_start', 'time_bin_size', 'a']: both
    # required columns are present and the two leading positions hold
    # ['x', 'time_bin_start'].
    assert str(exc.value) == order_message(
        'BinnedTimeSeries',
        ['time_bin_start', 'time_bin_size'],
        ['x', 'time_bin_start'])


def test_first_column_added_to_an_empty_binned_series_uses_the_missing_form():
    # Supersedes test_binned.py::test_empty_initialization_invalid.
    #
    # Judgement call: with a single, wrong column present, "the required column
    # names" can be read either as the class's full list, or - because a freshly
    # created empty series is in the relaxed state, where the columns are not
    # required to be present yet - as only as many of them as there are columns.
    # Both readings take the *missing* form, since no required column is
    # present; they differ only in how many names the clause holds, so this
    # accepts either.
    bts = BinnedTimeSeries()
    with pytest.raises(ValueError) as exc:
        bts['flux'] = [1, 2, 3]
    accepted = {
        missing_message('BinnedTimeSeries',
                        ['time_bin_start', 'time_bin_size']),
        missing_message('BinnedTimeSeries', ['time_bin_start']),
    }
    assert str(exc.value) in accepted
