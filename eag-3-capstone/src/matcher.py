"""Localise a camera frame on the orthomap: ORB + BFMatcher + RANSAC homography.

Deps: opencv-python, numpy, rasterio.

Heading convention (matches groundtruth.py): frames are built as
``map_patch.rotate(clockwise by heading)`` in PIL, so a frame's *up* axis points
at map direction ``(-sin h, -cos h)`` and heading is recovered as
``atan2(-vx, -vy)``.

Feature-density note: the orthomap is ~20 Mpx while a 200px frame covers only
~0.19% of it, so what limits matching is how many map keypoints *land inside the
frame footprint* -- not ``nfeatures``.  ORB saturates (~5.8k corners map-wide) no
matter how large a value you request.  The ORB default ``fastThreshold=20``
yields those ~5.8k corners (~11 per footprint); ``fastThreshold=5`` yields
~190k (~360 per footprint).  ``fastThreshold`` is the lever, not ``nfeatures``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np

from groundtruth import load_orthomap
from paths import PROJECT_ROOT, project_path, resolve_path
# --------------------------------------------------------------------------
# ORB / matching tuning
# ---------------------------------------------------------------------------
# Dense map features: a 200px frame occupies ~0.19% of the 20 Mpx map, so the
# map must be feature-dense for enough keypoints to fall inside it.
MAP_ORB = dict(nfeatures=200000, nlevels=1, fastThreshold=5, edgeThreshold=15)
# The frame is a small, same-scale crop, so it needs far fewer features.
FRAME_ORB = dict(nfeatures=1000, nlevels=1, fastThreshold=10, edgeThreshold=15)

RATIO = 0.80          # Lowe ratio test for the brute-force knn matcher
RANSAC_THRESH = 5.0   # px; frame and map are the same scale, so this is tight
MIN_INLIERS = 15      # below this the estimate is untrustworthy ("lucky pass")



# ---------------------------------------------------------------------------
# Run archiving -- every run is written out, nothing is ever overwritten
# ---------------------------------------------------------------------------
RUNS_DIR = PROJECT_ROOT / "eval" / "runs"

# A result counts as a pass only if it is accurate AND backed by enough inliers.
POS_TOL_M = 1.5
HEAD_TOL_DEG = 5.0


def _unique_run_path(when: "datetime") -> Path:
    """run_YYYY-MM-DD_HHMM.json, then _01, _02... if that minute already ran."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    base = f"run_{when:%Y-%m-%d_%H%M}"
    path = RUNS_DIR / f"{base}.json"
    suffix = 1
    while path.exists():
        path = RUNS_DIR / f"{base}_{suffix:02d}.json"
        suffix += 1
    return path

def _relative_to_root(path) -> str:
    """Return ``path`` relative to the project root when possible.

    Keeps run files portable between machines.  Anything outside the project
    (or already relative) is returned as given.
    """
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


def record_result(frame_name: Union[str, Path], estimate: Estimate, truth,
                  pos_tol_m: float = POS_TOL_M,
                  head_tol_deg: float = HEAD_TOL_DEG) -> dict:
    """Build one archived result row, with a three-way verdict.

    ``correct``          inliers >= MIN_INLIERS AND within position/heading tolerance
    ``unsure``           inliers <  MIN_INLIERS (the matcher can't vouch for it)
    ``confident_wrong``  inliers >= MIN_INLIERS but outside tolerance
    """
    t = truth.to_dict() if hasattr(truth, "to_dict") else dict(truth)
    t["map_path"] = _relative_to_root(t.get("map_path"))

    if estimate.inliers == 0 or math.isnan(estimate.px_x):
        return {
            "frame": Path(frame_name).name,
            "truth": t,
            "estimate": None,
            "position_error_m": None,
            "heading_error_deg": None,
            "inliers": estimate.inliers,
            "verdict": "unsure",
        }

    resolution = float(t["resolution"])
    pos_err_m = math.hypot(estimate.px_x - t["px_x"],
                           estimate.px_y - t["px_y"]) * resolution
    head_err = _wrap180(estimate.heading_deg - t["heading_deg"])

    if estimate.inliers < MIN_INLIERS:
        verdict = "unsure"
    elif pos_err_m <= pos_tol_m and abs(head_err) <= head_tol_deg:
        verdict = "correct"
    else:
        verdict = "confident_wrong"

    return {
        "frame": Path(frame_name).name,
        "truth": t,
        "estimate": estimate.to_dict(),
        "position_error_m": round(pos_err_m, 3),
        "heading_error_deg": round(head_err, 3),
        "inliers": estimate.inliers,
        "verdict": verdict,
    }


def save_run(results, map_path=None, pos_tol_m: float = POS_TOL_M,
             head_tol_deg: float = HEAD_TOL_DEG,
             when: "datetime" = None) -> Path:
    """Write one timestamped JSON run file and return its path.

    Never overwrites: two runs inside the same minute get _01, _02 suffixes.
    """
    when = when or datetime.now()
    results = list(results)

    payload = {
        "run_at": when.isoformat(timespec="seconds"),
        "map_path": _relative_to_root(map_path),
        "tolerances": {
            "position_m": pos_tol_m,
            "heading_deg": head_tol_deg,
            "min_inliers": MIN_INLIERS,
        },
        "summary": {
            "total": len(results),
            "correct": sum(1 for r in results if r["verdict"] == "correct"),
            "unsure": sum(1 for r in results if r["verdict"] == "unsure"),
            "confident_wrong": sum(1 for r in results
                                   if r["verdict"] == "confident_wrong"),
        },
        "results": results,
    }
    path = _unique_run_path(when)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path

__all__ = ["Estimate", "locate_frame", "project_path", "PROJECT_ROOT"]


@dataclass(frozen=True)
class Estimate:
    """Where the frame is believed to sit, in map pixel coordinates."""

    px_x: float
    px_y: float
    heading_deg: float
    inliers: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def locate_frame(
    frame_path: Union[str, Path],
    map_path: Union[str, Path],
    map_orb: Optional[dict] = None,
    frame_orb: Optional[dict] = None,
    ratio: float = RATIO,
    ransac_thresh: float = RANSAC_THRESH,
    probe_frac: float = 0.25,
    min_inliers: int = MIN_INLIERS,
    warn: bool = True,
) -> Estimate:
    """Estimate (px_x, px_y, heading_deg, inliers) for a frame on the map.

    Returns an :class:`Estimate` with ``inliers == 0`` and NaNs when the frame
    cannot be matched (too few keypoints, too few good matches, or no valid
    homography).  A warning is printed to stderr when the inlier count is below
    ``min_inliers``, because a homography fitted to ~4 points always "succeeds"
    and its numbers are meaningless.
    """
    map_rgb, _ = load_orthomap(map_path)
    map_gray = cv2.cvtColor(map_rgb, cv2.COLOR_RGB2GRAY)

    frame_bgr = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame_bgr is None:
        raise FileNotFoundError(f"Could not read frame: {frame_path}")
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    orb_map = cv2.ORB_create(**(map_orb or MAP_ORB))
    kp_map, des_map = orb_map.detectAndCompute(map_gray, None)
    orb_frame = cv2.ORB_create(**(frame_orb or FRAME_ORB))
    kp_frame, des_frame = orb_frame.detectAndCompute(frame_gray, None)

    failed = Estimate(px_x=float("nan"), px_y=float("nan"),
                      heading_deg=float("nan"), inliers=0)

    if des_map is None or des_frame is None:
        return failed
    if len(kp_map) < 4 or len(kp_frame) < 4:
        return failed

    # Brute-force Hamming match, frame -> map, with Lowe's ratio test.
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = bf.knnMatch(des_frame, des_map, k=2)
    good = [p[0] for p in knn if len(p) == 2 and p[0].distance < ratio * p[1].distance]
    if len(good) < 4:
        return failed

    src = np.float32([kp_frame[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_map[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    # Homography maps frame pixels -> map pixels; RANSAC rejects outliers.
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac_thresh,
                                 maxIters=10000, confidence=0.999)
    if H is None or mask is None:
        return failed

    inliers = int(mask.sum())
    if inliers < 4:
        return failed

    if warn and inliers < min_inliers:
        print(f"WARNING: only {inliers} inliers (< {min_inliers}) for "
              f"{Path(frame_path).name} -> estimate is unreliable",
              file=sys.stderr)

    # Position: map the frame centre through H.
    h, w = frame_gray.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    probe = max(1.0, probe_frac * min(w, h))
    pts = np.float32([[[cx, cy]], [[cx, cy - probe]]])  # centre and "up" from centre
    mapped = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    (mx, my), (ux, uy) = mapped[0], mapped[1]

    # Direction of the frame's up axis, expressed in map coordinates.
    vx, vy = ux - mx, uy - my
    heading = math.degrees(math.atan2(-vx, -vy)) % 360.0

    return Estimate(px_x=float(mx), px_y=float(my),
                    heading_deg=float(heading), inliers=inliers)


def _wrap180(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0

def _main(argv: list) -> int:
    if len(argv) < 4:
        print("usage: python matcher.py <frame.png> <frame.json> <map.tif>",
              file=sys.stderr)
        return 1

    frame_path = resolve_path(argv[1])
    truth_path = resolve_path(argv[2])
    map_path = resolve_path(argv[3])

    truth = json.loads(truth_path.read_text())
    resolution = float(truth["resolution"])

    est = locate_frame(frame_path, map_path)
    row = record_result(frame_path.name, est, truth)
    run_file = save_run([row], map_path=map_path)   # always archived

    if row["estimate"] is None:
        print("Localisation FAILED: not enough inlier matches.")
        print(f"truth    : px_x={truth['px_x']} px_y={truth['px_y']} "
              f"heading={truth['heading_deg']}")
        print(f"run archived -> {run_file}")
        return 2

    print(f"estimate : px_x={est.px_x:.2f} px_y={est.px_y:.2f} "
          f"heading={est.heading_deg:.2f} inliers={est.inliers}")
    print(f"truth    : px_x={truth['px_x']} px_y={truth['px_y']} "
          f"heading={truth['heading_deg']}")
    print(f"position error : {row['position_error_m']:.2f} m "
          f"({row['position_error_m'] / resolution:.2f} px)")
    print(f"heading error  : {row['heading_error_deg']:.2f} deg")
    print(f"verdict        : {row['verdict']}")
    print(f"run archived   -> {run_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))