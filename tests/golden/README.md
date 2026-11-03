# Golden baseline

`baseline.json` records the exact output of the original prototypes on synthetic
scenes. It holds 447 stable entries, recorded with Python 3.14.3 and NumPy 1.26.3
(MinGW build), and verified unchanged on NumPy 2.5.4 (MSVC build).

Do not re-record it to make a test pass. `record_baseline.py` refuses to replace an
existing file for that reason.

`sensitivity.json` is a separate supplement, also recorded from the unmodified
prototype. The scenes in `baseline.json` are mostly flat tones, which cannot detect
small changes to the weighting constants, so these scenes make hues compete across
different brightness and saturation levels.

## Why two adaptive entries are excluded

The first recording had 449 entries. These two differed between NumPy builds:

- `adaptive|small_frame|slices=9`
- `adaptive|small_frame|slices=15`

The cause is a defect in the experimental adaptive prototype, not numerical noise.
A 4-pixel-wide frame split into 9 or 15 slices produces empty slices. The median of
an empty slice is NaN, and casting NaN to an integer is undefined, so each NumPy
build returns a different out-of-range value (`-80000000` and `-8000000000000000`
when printed as hexadecimal).

Those values are not colors, so they cannot be a stable expectation. Every other
entry was identical between the two recordings, and the same 447 entries matched
across both NumPy builds. The stable modes (Atmospheric, Dominant colors and
Spatial) and all packet, smoothing and animation entries were unaffected.

The defect is tracked by the strict expected-failure test
`test_adaptive_handles_frames_narrower_than_its_slices`, which fails the suite with
an unexpected pass once the defect is fixed, so it cannot be forgotten.

The original 449-entry recording is kept locally for diagnostics in
`legacy/diagnostics/`, which is not published.
