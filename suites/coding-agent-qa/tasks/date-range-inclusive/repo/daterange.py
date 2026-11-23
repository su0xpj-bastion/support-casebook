from datetime import timedelta


def days_between(start, end):
    out = []
    d = start
    while d != end:
        out.append(d)
        d += timedelta(days=1)
    return out
