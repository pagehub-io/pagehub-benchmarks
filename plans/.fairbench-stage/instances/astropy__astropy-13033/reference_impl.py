"""Apply a reference implementation of the missing-columns requirement to core.py."""
import re, sys
p = "/testbed/astropy/timeseries/core.py"
s = open(p).read()
old_start = s.index("            plural = 's' if len(required_columns) > 1 else ''")
old_end = s.index("            if (self._required_columns_relax")
new = '''            def _render(names):
                names = list(names)
                return f"'{names[0]}'" if len(names) == 1 else str(names)

            missing = [c for c in required_columns if c not in self.colnames]
            if missing:
                s_ = 's' if len(missing) > 1 else ''
                raise ValueError(
                    f"{self.__class__.__name__} object is invalid - "
                    f"missing required column{s_} {_render(missing)}")

            if self.colnames[:len(required_columns)] != required_columns:
                s_ = 's' if len(required_columns) > 1 else ''
                raise ValueError(
                    f"{self.__class__.__name__} object is invalid - "
                    f"required column{s_} {_render(required_columns)} must come first, "
                    f"but found {_render(self.colnames[:len(required_columns)])}")

'''
open(p, "w").write(s[:old_start] + new + s[old_end:])
print("reference implementation applied")
