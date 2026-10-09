TimeSeries: misleading exception when required column check fails.
<!-- This comments are hidden when you submit the issue,
so you do not need to remove them! -->

<!-- Please be sure to check out our contributing guidelines,
https://github.com/astropy/astropy/blob/main/CONTRIBUTING.md .
Please be sure to check out our code of conduct,
https://github.com/astropy/astropy/blob/main/CODE_OF_CONDUCT.md . -->

<!-- Please have a search on our GitHub repository to see if a similar
issue has already been posted.
If a similar issue is closed, have a quick look to see if you are satisfied
by the resolution.
If not please go ahead and open an issue! -->

<!-- Please check that the development version still produces the same bug.
You can install development version with
pip install git+https://github.com/astropy/astropy
command. -->

### Description
<!-- Provide a general description of the bug. -->

For a `TimeSeries` object that has additional required columns (in addition to `time`), when codes mistakenly try to remove a required column, the exception it produces is misleading.

### Expected behavior
<!-- What did you expect to happen. -->
An exception that informs the users required columns are missing.

### Actual behavior
The actual exception message is confusing:
`ValueError: TimeSeries object is invalid - expected 'time' as the first columns but found 'time'`

### Steps to Reproduce
<!-- Ideally a code example could be provided so we can run it ourselves. -->
<!-- If you are pasting code, use triple backticks (```) around
your code snippet. -->
<!-- If necessary, sanitize your screen output to be pasted so you do not
reveal secrets like tokens and passwords. -->

```python
from astropy.time import Time
from astropy.timeseries import TimeSeries

time=Time(np.arange(100000, 100003), format='jd')
ts = TimeSeries(time=time, data = {"flux": [99.9, 99.8, 99.7]})
ts._required_columns = ["time", "flux"]                                   
ts.remove_column("flux")

```

### System Details
<!-- Even if you do not think this is necessary, it is useful information for the maintainers.
Please run the following snippet and paste the output below:
import platform; print(platform.platform())
import sys; print("Python", sys.version)
import numpy; print("Numpy", numpy.__version__)
import erfa; print("pyerfa", erfa.__version__)
import astropy; print("astropy", astropy.__version__)
import scipy; print("Scipy", scipy.__version__)
import matplotlib; print("Matplotlib", matplotlib.__version__)
-->
```
Windows-10-10.0.22000-SP0
Python 3.9.10 | packaged by conda-forge | (main, Feb  1 2022, 21:21:54) [MSC v.1929 64 bit (AMD64)]
Numpy 1.22.3
pyerfa 2.0.0.1
astropy 5.0.3
Scipy 1.8.0
Matplotlib 3.5.1
```

---

## Required behaviour

This section is part of the requirement. A solution is graded against it.

Raise `ValueError`. The message says **what is wrong**, in one of two forms.

**When any required column is absent from the object:**

```
{cls} object is invalid - missing required column{s} {missing}
```

**When every required column is present but not in the leading positions:**

```
{cls} object is invalid - required column{s} {required} must come first, but found {found}
```

| Variable | Value |
| --- | --- |
| `{cls}` | The class name of the object being checked. |
| `{missing}` | The required column names absent from the object, in the order they are required. |
| `{required}` | The required column names. |
| `{found}` | The column names actually occupying those leading positions. |
| `{s}` | `s` when the clause names more than one column, otherwise empty. It agrees with the names in that clause, not with the number required. |

Column names render by how many that variable holds:

- One name renders in single quotes, for example `'time'`.
- Several names render as a Python list, for example `['time', 'flux']`.

An object with no columns at all has every required column absent, so it takes
the missing form.

This base class is shared, so the change applies to every class using it,
`BinnedTimeSeries` included.

### Existing tests

Every existing test must still pass, **except** these three, which assert the
old message text and are superseded by this requirement. Update them to match:

- `astropy/timeseries/tests/test_sampled.py::test_required_columns`
- `astropy/timeseries/tests/test_sampled.py::test_empty_initialization_invalid`
- `astropy/timeseries/tests/test_binned.py::test_empty_initialization_invalid`

No other existing test may be modified.
