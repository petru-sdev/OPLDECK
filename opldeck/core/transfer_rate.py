"""Velocidade e previsão usando somente a janela recente de 30 segundos."""

from collections import deque


class TransferRate:
    def __init__(self):
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def observe(self, now, completed):
        if self.samples and completed < self.samples[-1][1]:
            self.reset()
        if self.samples and now == self.samples[-1][0]:
            self.samples[-1] = (now, completed)
        else:
            self.samples.append((now, completed))
        while len(self.samples) > 2 and self.samples[1][0] <= now - 30:
            self.samples.popleft()

    def at(self, moment):
        for left, right in zip(self.samples, list(self.samples)[1:]):
            if left[0] <= moment <= right[0]:
                fraction = (moment - left[0]) / (right[0] - left[0])
                return left[1] + fraction * (right[1] - left[1])
        return self.samples[0][1] if moment < self.samples[0][0] else self.samples[-1][1]

    def estimate(self, now, completed, total):
        self.observe(now, completed)
        start = max(self.samples[0][0], now - 30)
        elapsed = now - start
        rate = (completed - self.at(start)) / elapsed if elapsed > 0 else 0
        eta = max(0, total - completed) / rate if rate > 0 else None
        drop = False
        if elapsed >= 15 and completed < total:
            middle = now - 10
            baseline = (self.at(middle) - self.at(start)) / (middle - start)
            recent = (completed - self.at(middle)) / 10
            drop = baseline > 0 and recent < baseline * .5
        return max(0, rate), eta, drop
