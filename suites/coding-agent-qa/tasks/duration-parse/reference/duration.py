import re

_FULL = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def parse_duration(text):
    m = _FULL.match(text or "")
    if not m or not any(m.groups()):
        raise ValueError(f"bad duration: {text!r}")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s
