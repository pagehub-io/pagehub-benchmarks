"""Verifier for astropy__astropy-13033 under the missing-columns requirement.

Everything checked here is stated in the rewritten problem statement: the two
message forms, how each variable is filled, how names render by count, that the
noun agrees with the clause, that an empty object takes the missing form, and
that the shared base class makes this apply to BinnedTimeSeries too.

The expected message is derived from the stated rules rather than compared to a
literal, so a solver who reads the statement can predict every grade here.
"""
import pytest
from astropy.table import Column
from astropy.time import Time, TimeDelta
from astropy.timeseries.sampled import TimeSeries
from astropy.timeseries.binned import BinnedTimeSeries

INPUT_TIME = Time(["2016-03-22T12:30:31", "2015-01-21T12:30:32", "2016-03-22T12:30:40"])


def render(names):
    """One name in single quotes; several as a Python list. Stated."""
    names = list(names)
    return f"'{names[0]}'" if len(names) == 1 else str(names)


def missing_message(missing, cls="TimeSeries"):
    """The form used when any required column is absent."""
    s = "s" if len(missing) > 1 else ""
    return f"{cls} object is invalid - missing required column{s} {render(missing)}"


def order_message(required, found, cls="TimeSeries"):
    """The form used when all required columns are present but misplaced."""
    s = "s" if len(required) > 1 else ""
    return (f"{cls} object is invalid - required column{s} {render(required)} "
            f"must come first, but found {render(found)}")


def _ts(required=None):
    ts = TimeSeries(time=INPUT_TIME, data=[[10, 2, 3], [4, 5, 6]], names=["a", "b"])
    if required:
        ts._required_columns = required
    return ts


# -- the missing form ---------------------------------------------------------

def test_issue_example_names_the_missing_column():
    """The issue's own scenario: require time and flux, remove flux."""
    ts = TimeSeries(time=INPUT_TIME, data=[[1.0, 2.0, 3.0]], names=["flux"])
    ts._required_columns = ["time", "flux"]
    with pytest.raises(ValueError) as exc:
        ts.remove_column("flux")
    assert exc.value.args[0] == missing_message(["flux"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ts: ts.keep_columns(["a", "b"]),
        lambda ts: ts.remove_column("time"),
        lambda ts: ts.rename_column("time", "banana"),
    ],
)
def test_single_required_column_missing(mutate):
    ts = _ts()
    with pytest.raises(ValueError) as exc:
        mutate(ts)
    assert exc.value.args[0] == missing_message(["time"])


def test_several_missing_are_listed_in_required_order():
    ts = _ts(["time", "a", "b"])
    with pytest.raises(ValueError) as exc:
        ts.remove_columns(["a", "b"])
    assert exc.value.args[0] == missing_message(["a", "b"])


def test_empty_object_takes_the_missing_form():
    """No columns at all means every required column is absent. Stated."""
    ts = _ts()
    with pytest.raises(ValueError) as exc:
        ts.remove_columns(["time", "a", "b"])
    assert exc.value.args[0] == missing_message(["time"])


# -- the order form -----------------------------------------------------------

def test_present_but_not_first_uses_the_order_form():
    """Nothing is missing here: 'time' is still present, just not first."""
    ts = _ts()
    with pytest.raises(ValueError) as exc:
        ts.add_column(Column([3, 4, 5], name="c"), index=0)
    assert exc.value.args[0] == order_message(["time"], ["c"])


def test_order_form_with_several_required():
    ts = _ts(["time", "a"])
    with pytest.raises(ValueError) as exc:
        ts.add_column(Column([3, 4, 5], name="c"), index=0)
    assert exc.value.args[0] == order_message(["time", "a"], ["c", "time"])


# -- the shared base class ----------------------------------------------------

def test_applies_to_binned_time_series():
    """core.py is the shared base, so the requirement reaches BinnedTimeSeries."""
    bts = BinnedTimeSeries(
        time_bin_start=INPUT_TIME,
        time_bin_size=TimeDelta([1, 1, 1], format="sec"),
        data=[[10, 2, 3]],
        names=["a"],
    )
    with pytest.raises(ValueError) as exc:
        bts.remove_column("time_bin_start")
    assert exc.value.args[0] == missing_message(["time_bin_start"], cls="BinnedTimeSeries")
