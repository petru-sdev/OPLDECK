"""Durações legíveis, com transporte de segundos, minutos, horas e dias."""

import math


def duration_text(seconds):
    if seconds is None or not math.isfinite(seconds):
        return 'aguardando estimativa'
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    for value, unit in ((days, 'd'), (hours, 'h'), (minutes, 'min'), (seconds, 's')):
        if value:
            parts.append(f'{value} {unit}')
    return ' '.join(parts) or '0 s'
