# EAG-3 Capstone — Synthetic Orthomap Dataset & Visual Localisation Harness

Generate labelled synthetic drone camera frames from a georeferenced orthomap, then
score a GPS-free visual localisation matcher against them — and find out exactly
where that matcher breaks.

Every frame ships with its own answer key (`px_x`, `px_y`, `heading_deg`), so the
matcher is graded against truth rather than against itself.

---

## What it does

A drone that has lost GPS has to work out where it is from imagery alone. Building
that capability needs two things this repo provides:

1. **Labelled test data** — synthetic frames cut from a real orthomap, at known
   positions and headings, with controlled degradation.
2. **An honest scoring method** — one that separates *"correct"* from *"lucky"*,
   because a 4-point homography can land near the right answer by accident.

The four degradations (scale, blur, brightness, noise) are dialled independently,
so you can attribute a failure to one cause instead of a soup of them.

---

## How it works

```
orthomap (GeoTIFF)
      │
      │  groundtruth.py
      ▼
  crop → rotate → scale → blur → brightness → noise
      │
      ├──► data/snaps/<batch>/frame_XXXX_seedYY.png     the synthetic camera frame
      └──► data/snaps/<batch>/frame_XXXX_seedYY.json    the answer key
                 │
                 │  matcher.py
                 ▼
        ORB (frame + map) → BF-Hamming kNN → Lowe ratio test
                          → RANSAC homography → centre + up-probe
                 │
                 ▼
      estimate (px_x, px_y, heading_deg, inliers)
                 │
                 │  run_batch.py
                 ▼
        verdict: correct | unsure | confident_wrong
                 │
                 └──► eval/runs/run_<date>_<time>.json
```

**Localisation maths.** The homography maps frame pixels to map pixels. The frame
centre gives `(px_x, px_y)`; a second probe point one step "up" from the centre
gives the heading:

```
heading = degrees(atan2(-vx, -vy)) mod 360
```

---

## Requirements

Python 3.14. No frameworks — no PyTorch, no TensorFlow, no web stack.

```bash
pip install rasterio opencv-python numpy pillow
```

Versions used for the results below:

| package | version |
|---|---|
| numpy | 2.5.3 |
| opencv-python | 5.0.0.93 |
| rasterio | 1.5.1 |
| pillow | 12.3.0 |

---

## Data

The orthomap is **not committed** (25 MB, third-party source). Download it and place
it at:

```
data/maps/whitefield_bhuvan_hr_50cm_rgb_2022-24.tif
```

| property | value |
|---|---|
| size | 4523 × 4610 px (20.9 Mpx) |
| ground resolution | 0.30 m/px |
| bands | 3 (RGB) |
| source | Bhuvan / ISRO (Whitefield, Bengaluru) |

Any georeferenced RGB GeoTIFF works — pass its path as the first argument. Single-band
and 2-band rasters are promoted to RGB automatically.

---

## Quick start

```bash
# 1. frames only - no matching
./venv/bin/python src/groundtruth.py data/maps/whitefield_bhuvan_hr_50cm_rgb_2022-24.tif \
    --count 10 --scale hard

# 2. generate + score 10 frames, all degradations off (the baseline)
./venv/bin/python src/run_batch.py --count 10

# 3. one change at a time
./venv/bin/python src/run_batch.py --count 10 --scale hard
./venv/bin/python src/run_batch.py --count 10 --blur hard
./venv/bin/python src/run_batch.py --count 10 --brightness hard
./venv/bin/python src/run_batch.py --count 10 --noise hard

# 4. stack them
./venv/bin/python src/run_batch.py --count 10 --scale medium --blur easy --noise hard

# 5. single frame, verbose - prints estimate, truth, errors
./venv/bin/python src/matcher.py \
    data/snaps/scale-off_blur-off_bright-off_noise-off/frame_0000_seed42.png \
    data/snaps/scale-off_blur-off_bright-off_noise-off/frame_0000_seed42.json \
    data/maps/whitefield_bhuvan_hr_50cm_rgb_2022-24.tif
```

All scripts resolve paths against the project root, so they work from any directory.

`run_batch.py` exits `0` only if every frame came back `correct` — usable in CI:

```bash
./venv/bin/python src/run_batch.py --count 10 --scale easy || echo "regression"
```

---

## CLI reference

Both `groundtruth.py` and `run_batch.py` take the same four flags:

| flag | values | default | simulates |
|---|---|---|---|
| `--scale` | `off` `easy` `medium` `hard` | `off` | flying higher / lower |
| `--blur` | `off` `easy` `medium` `hard` | `off` | out-of-focus / motion blur |
| `--brightness` | `off` `easy` `medium` `hard` | `off` | under/over-exposure |
| `--noise` | `off` `easy` `medium` `hard` | `off` | sensor noise |
| `--count` | integer | `10` (`groundtruth.py`: `1`) | number of frames |

Seeds always start at **42** and step by one (`--count 10` → seeds 42–51). There is no
`--seed` flag.

`run_batch.py` accepts an optional positional map path (defaults to the Whitefield map);
`groundtruth.py` requires it.

---

## Degradation levels

Each level maps to a range; the **seed** picks the value inside it. So `--scale hard`
gives a different factor for every seed — that is the variation.

| knob | off | easy | medium | hard |
|---|---|---|---|---|
| `scale` | 1.00 | 0.95 – 1.05 | 0.85 – 1.15 | **0.70 – 1.30** |
| `blur` (Gaussian radius, px) | 0 | 0 – 0.5 | 0.5 – 1.0 | **1.0 – 2.0** |
| `brightness` (multiplier) | 1.00 | 0.90 – 1.10 | 0.75 – 1.25 | **0.60 – 1.40** |
| `noise` (Gaussian σ) | 0 | 0 – 0.010 | 0.010 – 0.030 | **0.030 – 0.060** |

Scale convention: `eff_px = crop_size_px / scale`, so **scale < 1 flies higher** (the
frame covers more ground, then downsamples) and **scale > 1 flies lower** (less ground,
upsampled).

Effects are applied in a fixed order: **crop → rotate → scale → blur → brightness →
noise**, with pixels clipped to 0–255.

---

## Output layout

```
data/snaps/scale-<s>_blur-<b>_bright-<r>_noise-<n>/
├── frame_0000_seed42.png     synthetic camera frame
├── frame_0000_seed42.json    ground truth, same stem
└── ...

eval/runs/
├── run_2026-09-27_2142.json          one file per run, never overwritten
└── run_2026-09-27_2142_01.json       _01, _02 ... on same-minute collisions
```

Ground-truth JSON:

```json
{
  "map_path": "data/maps/whitefield_bhuvan_hr_50cm_rgb_2022-24.tif",
  "px_x": 2910, "px_y": 2043,
  "heading_deg": 278.624,
  "crop_size_px": 200,
  "resolution": 0.3,
  "scale_level": "hard", "scale": 1.1761,
  "blur_level": "off",   "blur": 0.0,
  "brightness_level": "off", "brightness": 1.0,
  "noise_level": "off",  "noise": 0.0,
  "seed": 42
}
```

Both the level name and the actual drawn value are stored, so a run is reproducible and
self-describing.

---

## Scoring

`matcher.py` returns the inlier count alongside the estimate. That matters: a
homography built on 4 accidental matches can land within a metre and still be
meaningless. Three verdicts instead of a pass/fail bit:

| verdict | condition | meaning |
|---|---|---|
| `correct` | inliers ≥ 15 **and** pos ≤ 1.5 m **and** \|heading\| ≤ 5° | trustworthy |
| `unsure` | inliers < 15 | the matcher cannot vouch for the result |
| `confident_wrong` | inliers ≥ 15 **but** outside tolerance | **the dangerous case** — a confident, wrong fix |

Tolerances: `POS_TOL_M = 1.5`, `HEAD_TOL_DEG = 5.0`, `MIN_INLIERS = 15`.
Position error is `pixel error × resolution`.

---

## Measured results

3 frames per config (seeds 42–44), 200 px crop, 0.30 m/px. A smoke test, not a full
evaluation — but it already shows the shape of the problem.

| config | median pos error | median \|heading error\| | median inliers |
|---|---|---|---|
| all off (baseline) | 0.25 m | 0.16° | 49 |
| scale easy | 0.34 m | 0.12° | 39 |
| scale medium | 0.33 m | 0.16° | 30 |
| **scale hard** | **17.22 m** | **149.90°** | 27 |
| blur hard | 0.76 m | 1.07° | 8 |
| brightness hard | 0.23 m | 0.17° | 37 |
| noise hard | 0.30 m | 0.04° | 91 |

Per-frame detail for `scale hard`, which is where it falls apart:

| frame | actual scale | inliers | verdict |
|---|---|---|---|
| seed 42 | 1.1761 | 4 | unsure |
| seed 43 | 1.2385 | 0 | unsure |
| seed 44 | 0.9302 | 27 | correct |

Repeated three times, byte-identical. Two observations worth carrying into the report:

- **Scale is the dominant failure axis**, not blur or noise. Noise at `hard` actually
  *raises* inliers (91 vs 49) — dithering manufactures corners.
- The failing seeds were the ones that **zoomed in**; the one that zoomed slightly out
  survived. See the limitation below.

---

## Known limitations

**The matcher is deliberately same-scale.** `MAP_ORB`/`FRAME_ORB` use `nlevels=1`
(no scale pyramid) and `RANSAC_THRESH = 5.0` px assumes frame and map are at the same
ground scale. A scale factor beyond roughly ±10 % therefore falls outside its design
envelope. The `scale hard` rows above measure **that limit**, not how bad the imagery
looks. Making it scale-robust (`nlevels=3+`, looser threshold, or matching on a
pyramid) is the obvious next step — and a good one to state as future work.

**Feature-starved regions.** The orthomap has nodata and deep-shadow strips near its
borders. A frame centred there yields almost no keypoints (seed 43 above got 0), and
no matcher can fix that. The `unsure` verdict is the system correctly reporting it.

**Position invariance has preconditions.** Same seed → same `px_x`, `px_y`,
`heading_deg` in every batch, verified across 10 seeds × 6 configs. This holds for a
fixed `crop_size_px` and map, because the safe-placement bounds depend on both.
Explicitly passing `heading_deg` also changes the bounds, so it is not comparable to a
seeded call.

**Small sample.** The tables above are 3 frames per config. Widen `--count` before
quoting numbers in the report.

---

## Reproducibility guarantees

- **Seed-locked position.** `px_x`, `px_y` and `heading_deg` are drawn from a dedicated
  generator stream (`[seed, 0]`) and are drawn *first*, in a fixed order. Turning any
  degradation on or off never moves the frame.
- **Isolated streams.** Effect values use `[seed, 1]`; pixel noise uses `[seed, 2]`.
  One effect can never perturb another.
- **Worst-case placement bounds.** Centre bounds are sized from the smallest possible
  scale (0.70), which samples the most ground and needs the largest source patch. If a
  patch ever fails to fit, the code raises rather than silently sliding the crop off
  its true centre — a silent shift would corrupt the answer key.
- **Append-only results.** Runs are timestamped and never overwritten.

---

## Project structure

```
eag-3-capstone/
├── src/
│   ├── paths.py          project-root-relative path helpers (cwd-independent)
│   ├── groundtruth.py    orthomap loading, frame sampling, ground truth
│   ├── matcher.py        ORB + RANSAC localisation, verdicts, run archiving
│   ├── run_batch.py      generate N frames, score them, print a summary table
│   ├── harness.py        (planned) end-to-end experiment runner
│   ├── filter.py         (planned) result filtering / outlier rejection
│   └── agent/            (planned) perception → memory → decision → action
├── data/
│   ├── maps/             orthomap GeoTIFF (not committed)
│   └── snaps/            generated frames + answer keys (not committed)
├── eval/
│   ├── runs/             archived run JSON (not committed)
│   ├── tasks/            (planned)
│   └── mutations/        (planned)
└── tests/                (planned)
```

---

## Roadmap

- [ ] Scale-robust matching (multi-level ORB or log-polar / scale-space search)
- [ ] `harness.py` — run a full degradation grid, aggregate into one report
- [ ] `filter.py` — reject degenerate homographies before they become a fix
- [ ] `agent/` — close the loop: perception → memory → decision → action
- [ ] Widen to 50+ frames per config and publish a confidence interval
- [ ] Second orthomap (different city / resolution) to prove it is not overfit

---

## Credits

Orthomap imagery courtesy of **Bhuvan / ISRO** (Whitefield, Bengaluru, 2022–24).