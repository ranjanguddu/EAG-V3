"""Synthetic camera frames + ground truth from a georeferenced orthomap.

Each of four degradations can be dialled independently with its own command
line setting -- scale, blur, brightness, noise -- each accepting
``off`` / ``easy`` / ``medium`` / ``hard``.

Design guarantees:
* **Stable position.** px_x, px_y and heading come from a dedicated generator
  and depend only on the seed, never on the degradation settings.  Turning blur
  on does not move the snap.
* **Safe at every scale.** Centre bounds are computed from the worst-case
  (smallest) scale, so the source crop can never run off the map edge.
* **The centre is the truth.** (px_x, px_y) is the centre of the returned
  frame at every scale, because the crop is taken about that point before any
  resize.
* **Fixed order.** crop -> rotate -> scale -> blur -> brightness -> noise, with
  pixel values clipped to 0-255.

Dependencies: ``rasterio``, ``PIL`` (Pillow) and ``numpy`` only.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
import rasterio
from PIL import Image, ImageFilter

from paths import PROJECT_ROOT, resolve_path

__all__ = ["GroundTruth", "batch_tag", "effect_values", "load_orthomap", "sample_frame"]

# ---------------------------------------------------------------------------
# Settings and ranges
# ---------------------------------------------------------------------------
_OPTION_KEYS = ("scale", "blur", "brightness", "noise")
_LEVELS = ("off", "easy", "medium", "hard")

SCALE_RANGES = {"off": (1.00, 1.00), "easy": (0.95, 1.05),
                "medium": (0.85, 1.15), "hard": (0.70, 1.30)}
BLUR_RANGES = {"off": (0.0, 0.0), "easy": (0.0, 0.5),
               "medium": (0.5, 1.0), "hard": (1.0, 2.0)}
BRIGHTNESS_RANGES = {"off": (1.00, 1.00), "easy": (0.90, 1.10),
                     "medium": (0.75, 1.25), "hard": (0.60, 1.40)}
NOISE_RANGES = {"off": (0.000, 0.000), "easy": (0.000, 0.010),
                "medium": (0.010, 0.030), "hard": (0.030, 0.060)}

_RANGES_BY_NAME = {"scale": SCALE_RANGES, "blur": BLUR_RANGES,
                   "brightness": BRIGHTNESS_RANGES, "noise": NOISE_RANGES}

# The smallest scale any level can produce ("hard"), and therefore the one that
# samples the most ground and needs the biggest source patch.  Centre bounds are
# sized for this worst case so the crop fits at every scale -- and so the bounds
# never depend on the scale actually drawn.
WORST_CASE_SCALE = SCALE_RANGES["hard"][0]

# Independent generator streams, so one effect can never perturb another.
_POS_STREAM, _EFFECT_STREAM, _NOISE_STREAM = 0, 1, 2


def _check_level(name: str, level: str) -> str:
    if level not in _LEVELS:
        raise ValueError(f"{name} must be one of {_LEVELS}, got {level!r}")
    return level


def effect_values(
    seed: int,
    scale_level: str = "off",
    blur_level: str = "off",
    brightness_level: str = "off",
    noise_level: str = "off",
) -> dict:
    """Draw one value per effect from a dedicated generator stream.

    All four draws always happen, in a fixed order, so a given effect's value
    never depends on the other three settings.  ``off`` uses a zero-width range
    and therefore yields the identity value (1.0 / 0 / 1.0 / 0).
    """
    for name, level in (("scale", scale_level), ("blur", blur_level),
                        ("brightness", brightness_level), ("noise", noise_level)):
        _check_level(name, level)

    rng = np.random.default_rng([int(seed), _EFFECT_STREAM])
    return {
        "scale": round(float(rng.uniform(*SCALE_RANGES[scale_level])), 4),
        "blur": round(float(rng.uniform(*BLUR_RANGES[blur_level])), 4),
        "brightness": round(float(rng.uniform(*BRIGHTNESS_RANGES[brightness_level])), 4),
        "noise": round(float(rng.uniform(*NOISE_RANGES[noise_level])), 4),
    }


def batch_tag(scale_level: str = "off", blur_level: str = "off",
              brightness_level: str = "off", noise_level: str = "off") -> str:
    """Folder name for a batch, e.g. ``scale-hard_blur-off_bright-off_noise-off``."""
    return (f"scale-{scale_level}_blur-{blur_level}"
            f"_bright-{brightness_level}_noise-{noise_level}")


def _relative_to_root(path) -> str:
    """Store paths relative to the project root so runs are portable."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_absolute():
        # Already project-relative; resolving it here would anchor it to the
        # current directory instead, so leave it untouched.
        return str(p)
    try:
        return str(p.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Map loading
# ---------------------------------------------------------------------------
def load_orthomap(map_path: Union[str, Path]) -> Tuple[np.ndarray, float]:
    """Load a georeferenced orthomap and return ``(rgb, resolution_m_per_px)``.

    Any band count is accepted: single-band and two-band rasters are promoted to
    RGB, rasters with more than three bands keep their first three.
    """
    map_path = Path(map_path)
    if not map_path.exists():
        raise FileNotFoundError(f"Orthomap not found: {map_path}")

    with rasterio.open(map_path) as src:
        data = src.read()          # (bands, H, W)
        transform = src.transform

    if data.ndim != 3:
        raise ValueError(f"Unexpected raster shape {data.shape}; expected (bands, H, W)")
    if data.shape[0] == 0:
        raise ValueError(f"Raster {map_path} has no bands")

    if data.shape[0] == 1:
        data = np.repeat(data, 3, axis=0)
    elif data.shape[0] == 2:
        data = np.concatenate([data, data[:1]], axis=0)
    elif data.shape[0] > 3:
        data = data[:3]

    if data.dtype != np.uint8:
        if np.issubdtype(data.dtype, np.floating) and data.size and data.max() <= 1.0:
            data = data * 255.0
        data = np.clip(np.rint(data), 0, 255).astype(np.uint8)

    rgb = np.ascontiguousarray(np.transpose(data, (1, 2, 0)))
    resolution = float((abs(transform.a) + abs(transform.e)) / 2.0)
    if resolution <= 0:
        raise ValueError(f"Invalid pixel resolution {resolution} for {map_path}")
    return rgb, resolution


# ---------------------------------------------------------------------------
# Ground-truth record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GroundTruth:
    """Answer key for a single synthetic camera frame."""

    map_path: str
    px_x: int
    px_y: int
    heading_deg: float
    crop_size_px: int
    resolution: float
    scale_level: str
    scale: float
    blur_level: str
    blur: float
    brightness_level: str
    brightness: float
    noise_level: str
    noise: float
    seed: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------
def _source_patch_size(crop_size_px: int, heading_deg: float, margin_px: int = 2) -> int:
    """Smallest square patch whose rotation fully covers a centred crop square.

    An axis-aligned square of side ``C`` rotated by ``theta`` is covered by a
    centred axis-aligned square of side ``C * (|cos t| + |sin t|)``, which peaks
    at ``sqrt(2)`` for a 45 degree heading.
    """
    theta = math.radians(heading_deg)
    return int(math.ceil(crop_size_px * (abs(math.cos(theta)) + abs(math.sin(theta))))) + margin_px


def sample_frame(
    map_path: Union[str, Path],
    crop_size_px: int = 200,
    seed: Optional[int] = None,
    px_x: Optional[float] = None,
    px_y: Optional[float] = None,
    heading_deg: Optional[float] = None,
    scale_level: str = "off",
    blur_level: str = "off",
    brightness_level: str = "off",
    noise_level: str = "off",
) -> Tuple[Image.Image, GroundTruth]:
    """Sample one synthetic frame and return ``(PIL Image, GroundTruth)``.

    ``px_x``/``px_y``/``heading_deg`` default to seeded random values inside the
    safe bounds.  They depend only on ``seed`` -- never on the settings -- so a
    given seed is always the same place.
    """
    if crop_size_px < 1:
        raise ValueError(f"crop_size_px must be >= 1, got {crop_size_px}")

    if seed is None:
        seed = int(np.random.default_rng().integers(0, 2**31 - 1))
    seed = int(seed)

    rgb, resolution = load_orthomap(map_path)
    height, width = rgb.shape[:2]

    # --- position: its own stream, and bounds sized for the worst-case scale ---
    pos_rng = np.random.default_rng([seed, _POS_STREAM])

    # All of heading, px_x and px_y are drawn from this one stream, always in
    # this order, so the same seed yields the same place regardless of which of
    # them the caller supplies.  Explicit values override the draw without
    # shifting the stream.
    drawn_heading = float(pos_rng.uniform(0.0, 360.0))
    if heading_deg is None:
        heading_deg = drawn_heading
    heading_deg = float(heading_deg) % 360.0

    eff_max = int(math.ceil(crop_size_px / WORST_CASE_SCALE))
    patch_max = _source_patch_size(eff_max, heading_deg)
    if patch_max > width or patch_max > height:
        raise ValueError(
            f"crop_size_px={crop_size_px} needs a {patch_max}px worst-case patch, "
            f"but the map is only {width}x{height}px"
        )

    half = patch_max / 2.0
    min_x, max_x = half, width - half
    min_y, max_y = half, height - half
    low_x, high_x = int(math.ceil(min_x)), int(math.floor(max_x))
    low_y, high_y = int(math.ceil(min_y)), int(math.floor(max_y))

    drawn_x = int(pos_rng.integers(low_x, high_x + 1))
    drawn_y = int(pos_rng.integers(low_y, high_y + 1))

    if px_x is None:
        px_x = drawn_x
    else:
        if not min_x <= px_x <= max_x:
            raise ValueError(f"px_x={px_x} outside safe range [{min_x:.2f}, {max_x:.2f}]")
        px_x = int(round(px_x))

    if px_y is None:
        px_y = drawn_y
    else:
        if not min_y <= px_y <= max_y:
            raise ValueError(f"px_y={px_y} outside safe range [{min_y:.2f}, {max_y:.2f}]")
        px_y = int(round(px_y))

    # --- degradation values: separate stream, fixed draw order ---
    values = effect_values(seed, scale_level, blur_level, brightness_level, noise_level)
    scale = values["scale"]
    blur = values["blur"]
    brightness = values["brightness"]
    noise = values["noise"]

    # --- 1. crop (about the true centre) ------------------------------------
    eff_px = max(2, int(round(crop_size_px / float(scale))))
    patch_px = _source_patch_size(eff_px, heading_deg)
    x0 = int(round(px_x - patch_px / 2.0))
    y0 = int(round(px_y - patch_px / 2.0))
    # The centre bounds above were sized for the worst-case scale, so the patch
    # must always fit.  Clamping here instead would slide the crop off its true
    # centre and quietly corrupt the answer key, so fail loudly.
    if not (0 <= x0 and x0 + patch_px <= width
            and 0 <= y0 and y0 + patch_px <= height):
        raise ValueError(
            f"source patch {patch_px}px at ({x0},{y0}) runs off the {width}x{height} "
            f"map for px=({px_x},{px_y}), scale={scale}, heading={heading_deg}"
        )
    patch = rgb[y0 : y0 + patch_px, x0 : x0 + patch_px]

    # --- 2. rotate (clockwise by heading; expand keeps data, not black) ------
    rotated = Image.fromarray(patch, mode="RGB").rotate(
        -heading_deg, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0)
    )

    # --- 3. scale (centre-crop the zoomed area, then resample) --------------
    rw, rh = rotated.size
    left = (rw - eff_px) // 2
    top = (rh - eff_px) // 2
    frame = rotated.crop((left, top, left + eff_px, top + eff_px))
    if eff_px != crop_size_px:
        frame = frame.resize((crop_size_px, crop_size_px), Image.LANCZOS)

    # --- 4. blur -----------------------------------------------------------
    if blur > 0.0:
        frame = frame.filter(ImageFilter.GaussianBlur(radius=float(blur)))

    # --- 5. brightness -----------------------------------------------------
    if brightness != 1.0:
        pixels = np.asarray(frame, dtype=np.float32) * float(brightness)
        frame = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), mode="RGB")

    # --- 6. noise ----------------------------------------------------------
    if noise > 0.0:
        pixels = np.asarray(frame, dtype=np.float32)
        sigma = float(noise) * 255.0
        pixels = pixels + np.random.default_rng([seed, _NOISE_STREAM]).normal(
            0.0, sigma, size=pixels.shape
        )
        frame = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), mode="RGB")

    truth = GroundTruth(
        map_path=_relative_to_root(map_path),
        px_x=int(px_x),
        px_y=int(px_y),
        heading_deg=round(float(heading_deg), 3),
        crop_size_px=int(crop_size_px),
        resolution=round(float(resolution), 6),
        scale_level=scale_level,
        scale=round(float(scale), 4),
        blur_level=blur_level,
        blur=round(float(blur), 4),
        brightness_level=brightness_level,
        brightness=round(float(brightness), 4),
        noise_level=noise_level,
        noise=round(float(noise), 4),
        seed=seed,
    )
    return frame, truth


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
_USAGE = """usage: python groundtruth.py <map_path> [options]

  --count N              number of snaps, seeds start at 42 (default 1)
  --scale   off|easy|medium|hard     (default off)
  --blur    off|easy|medium|hard     (default off)
  --brightness off|easy|medium|hard  (default off)
  --noise   off|easy|medium|hard     (default off)

Writes data/snaps/scale-<s>_blur-<b>_bright-<r>_noise-<n>/frame_XXXX_seedYY.{png,json}
"""


def parse_args(argv: list) -> Tuple[dict, list]:
    """Parse ``--key value`` / ``--key=value`` flags; return (options, positionals)."""
    opts = {"count": 1, **{k: "off" for k in _OPTION_KEYS}}
    positionals, i = [], 1
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                key, value = key.split("=", 1)
                i += 1
            else:
                if i + 1 >= len(argv):
                    raise SystemExit(f"{arg} needs a value")
                value = argv[i + 1]
                i += 2
            if key == "count":
                opts["count"] = int(value)
            elif key in _OPTION_KEYS:
                opts[key] = _check_level(key, str(value).lower())
            else:
                raise SystemExit(f"unknown option --{key}")
        else:
            positionals.append(arg)
            i += 1
    return opts, positionals


if __name__ == "__main__":
    opts, positionals = parse_args(sys.argv)
    if not positionals:
        print(_USAGE, file=sys.stderr)
        raise SystemExit(1)

    map_path = resolve_path(positionals[0])
    count = int(opts["count"])
    levels = {f"{k}_level": opts[k] for k in _OPTION_KEYS}

    out_dir = PROJECT_ROOT / "data" / "snaps" / batch_tag(
        opts["scale"], opts["blur"], opts["brightness"], opts["noise"]
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    for i in range(count):
        seed = 42 + i
        frame, truth = sample_frame(map_path, seed=seed, **levels)
        stem = f"frame_{i:04d}_seed{seed}"
        frame.save(out_dir / f"{stem}.png")
        (out_dir / f"{stem}.json").write_text(truth.to_json(), encoding="utf-8")

    print(f"Saved {count} frame(s) to {out_dir}")