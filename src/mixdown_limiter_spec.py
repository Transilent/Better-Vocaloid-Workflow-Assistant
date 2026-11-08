"""Final limiter contract shared by export and continuous audition."""

PEAK_CEILING_DBFS = -1.0
PEAK_CEILING = 10.0 ** (PEAK_CEILING_DBFS / 20.0)
ATTACK_MS = 5.0
RELEASE_MS = 100.0


def ffmpeg_filter():
    return (f'alimiter=limit={PEAK_CEILING:.9f}:attack={ATTACK_MS:g}:'
            f'release={RELEASE_MS:g}:level=false:latency=true:asc=false')
