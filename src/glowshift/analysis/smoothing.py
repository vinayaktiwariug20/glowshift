"""Exponential smoothing between analyzed frames, extracted from the capture loop."""
import numpy as np


class ColorSmoother:
    """Low-pass filter giving gradual change even across sample boundaries."""

    def __init__(self, transition):
        self.transition = transition
        self.smoothed = None

    def update(self, target, elapsed):
        """Advance by ``elapsed`` seconds toward ``target``; return RGB tuples."""
        if self.smoothed is None:
            self.smoothed = target.copy()
        dt = max(0.0, min(1.0, elapsed))
        tau = max(0.15, self.transition)
        alpha = 1 - np.exp(-dt / tau)
        self.smoothed += (target - self.smoothed) * alpha
        return [tuple(int(x) for x in np.clip(row, 0, 255)) for row in np.rint(self.smoothed)]
