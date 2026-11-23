import re

_PART = re.compile(r"(\d+)([hms])")


def parse_duration(text):
    total = 0
    for value, unit in _PART.findall(text):
        total += int(value) * {"h": 3600, "m": 1, "s": 1}[unit]
    return total
