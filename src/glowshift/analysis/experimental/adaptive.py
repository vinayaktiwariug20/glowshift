"""EXPERIMENTAL offline prototype: adaptively map an RGB image to 15 ordered LED colors.

Not a stable mode: it is not selectable through ``analyze_frame``. It needs Pillow, and
has a known defect on images narrower than the slice count. See tests/golden/README.md.

Run: python -m glowshift.analysis.experimental.adaptive screenshot.png --output palette.png
"""
from __future__ import annotations

import argparse
import colorsys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


@dataclass
class ColorCandidate:
    rgb: tuple[int, int, int]
    coverage: float
    salience: float
    y: float


def _candidates(region: np.ndarray, max_colors: int = 3) -> list[ColorCandidate]:
    """Histogram-based hue grouping; include near-neutral pixels as a separate group."""
    rgb = region.reshape(-1, 3).astype(np.float32) / 255
    mx, mn = rgb.max(1), rgb.min(1)
    delta = mx - mn
    sat = delta / np.maximum(mx, 1e-6)
    hue = np.zeros(len(rgb), dtype=np.float32)
    nonzero = delta > 1e-5
    r, g, b = rgb.T
    rm = nonzero & (mx == r)
    gm = nonzero & (mx == g) & ~rm
    bm = nonzero & ~rm & ~gm
    hue[rm] = ((g[rm] - b[rm]) / delta[rm]) % 6
    hue[gm] = (b[gm] - r[gm]) / delta[gm] + 2
    hue[bm] = (r[bm] - g[bm]) / delta[bm] + 4
    hue = hue / 6
    # 18 hue bins + two neutral bins; hue bins are grouped in pairs to reduce noise.
    bins = np.where(sat < .14, 18 + (mx >= .55).astype(int), (hue * 18).astype(int) % 18)
    y_positions = np.repeat(np.arange(region.shape[0]), region.shape[1]) / max(1, region.shape[0] - 1)
    candidates = []
    total = len(rgb)
    for k in range(20):
        mask = bins == k
        count = int(mask.sum())
        if count < max(6, total * .018):
            continue
        coverage = count / total
        colors = rgb[mask]
        # Avoid averaging wildly varying brightness into black; preserve representative hue.
        representative = np.percentile(colors, 65, axis=0)
        representative = np.clip(representative, 0, 1)
        s = np.median(sat[mask])
        v = np.median(mx[mask])
        salience = coverage * (.42 + .58 * min(1.0, s * 1.6)) * (.6 + .4 * min(1, v * 1.5))
        candidates.append(ColorCandidate(tuple(np.rint(representative * 255).astype(int)),
                                         coverage, float(salience), float(np.mean(y_positions[mask]))))
    if not candidates:
        representative = tuple(np.median(region.reshape(-1, 3), axis=0).astype(int))
        return [ColorCandidate(representative, 1.0, 1.0, .5)]
    # Consolidate adjacent hue bins into color families, including hue wraparound.
    candidates.sort(key=lambda c: c.salience, reverse=True)
    selected = []
    for candidate in candidates:
        if any(_color_distance(candidate.rgb, other.rgb) < 50 for other in selected):
            continue
        selected.append(candidate)
        if len(selected) == max_colors:
            break
    return selected or candidates[:1]


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    # Weighted RGB distance is intentionally cheap for this initial prototype.
    d = np.array(a, dtype=float) - np.array(b, dtype=float)
    return float(np.sqrt(.30 * d[0] ** 2 + .59 * d[1] ** 2 + .11 * d[2] ** 2))


def adaptive_palette(image: Image.Image, slices: int = 9, leds: int = 15) -> list[tuple[int, int, int]]:
    """One slot per vertical slice; allocate spare slots to significant secondary hues.

    Extra colors in a slice are ordered top-to-bottom. This preserves horizontal
    ordering but intentionally cannot preserve full 2D geometry.
    """
    if not (1 <= slices <= leds):
        raise ValueError('slices must be between 1 and leds')
    im = image.convert('RGB')
    im.thumbnail((640, 360))
    arr = np.asarray(im)
    # Avoid HUD/window borders; do not crop aggressively in bright games.
    h, w = arr.shape[:2]
    arr = arr[int(h * .03):max(int(h * .03) + 1, int(h * .97)), :]
    sections = np.array_split(arr, slices, axis=1)
    choices = [_candidates(section) for section in sections]
    allocations = [[c[0]] for c in choices]
    remaining = leds - slices
    # Global priority allocation: secondary colors need both salience and
    # meaningful coverage relative to the dominant color in their own slice.
    options = []
    for x, candidates in enumerate(choices):
        for candidate in candidates[1:]:
            ratio = candidate.salience / max(candidates[0].salience, 1e-8)
            if candidate.coverage >= .045 and ratio >= .18:
                options.append((candidate.salience * (0.5 + min(1.0, ratio)), x, candidate))
    options.sort(key=lambda t: t[0], reverse=True)
    for _, x, candidate in options[:remaining]:
        allocations[x].append(candidate)
    # If there aren't enough distinct colors, distribute remaining slots to
    # preserve horizontal space instead of inventing fake hues.
    while sum(map(len, allocations)) < leds:
        x = min(range(slices), key=lambda i: (len(allocations[i]), i))
        allocations[x].append(allocations[x][0])
    result = []
    for group in allocations:
        # Vertical ordering is arbitrary when multiple colors share a column;
        # top-to-bottom is a deterministic convention.
        for c in sorted(group, key=lambda item: item.y):
            result.append(c.rgb)
    return result[:leds]


def save_preview(colors: list[tuple[int, int, int]], path: Path) -> None:
    width, height = 900, 105
    canvas = Image.new('RGB', (width, height), '#202020')
    draw = ImageDraw.Draw(canvas)
    for i, color in enumerate(colors):
        x0, x1 = round(i * width / len(colors)), round((i + 1) * width / len(colors))
        draw.rectangle((x0 + 1, 1, x1 - 2, 76), fill=color)
        draw.text((x0 + 19, 83), str(i + 1), fill='white')
    canvas.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--slices', type=int, default=9)
    parser.add_argument('--leds', type=int, default=15)
    parser.add_argument('--output', type=Path, default=Path('adaptive_palette.png'))
    args = parser.parse_args()
    colors = adaptive_palette(Image.open(args.image), args.slices, args.leds)
    save_preview(colors, args.output)
    print('RGB palette:', colors)
    print('Preview saved:', args.output)


if __name__ == '__main__':
    main()
