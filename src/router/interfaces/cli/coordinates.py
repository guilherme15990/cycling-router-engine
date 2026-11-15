"""Keep comma-separated negative coordinates from looking like CLI options."""
from __future__ import annotations

import sys


def normalize_coordinate_args(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    result = []
    index = 0
    while index < len(values):
        flag = values[index]
        if flag in {"--bbox", "--origin", "--destination", "--stop"} and index + 1 < len(values):
            value = values[index + 1]
            if value.startswith("-") and "," in value:
                result.append(flag + "=" + value)
                index += 2
                continue
        result.append(flag)
        index += 1
    return result
