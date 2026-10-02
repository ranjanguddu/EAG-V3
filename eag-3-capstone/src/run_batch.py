"""Generate N synthetic snaps and score matcher.py on each of them.

Usage:
    python src/run_batch.py [map_path] [options]

  --count N              number of snaps, seeds start at 42 (default 10)
  --scale      off|easy|medium|hard   (default off)
  --blur       off|easy|medium|hard   (default off)
  --brightness off|easy|medium|hard   (default off)
  --noise      off|easy|medium|hard   (default off)

Writes data/snaps/scale-<s>_blur-<b>_bright-<r>_noise-<n>/frame_XXXX_seedYY.{png,json}
and one archived run file in eval/runs/ containing every result.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import groundtruth as gt                       # noqa: E402
import matcher as mt                           # noqa: E402
from paths import project_path, resolve_path   # noqa: E402

# Cache the 26 MB map so N snaps do not re-read/re-decode it N times.
gt.load_orthomap = functools.lru_cache(maxsize=1)(gt.load_orthomap)

DEFAULT_MAP = project_path("data", "maps", "whitefield_bhuvan_hr_50cm_rgb_2022-24.tif")


def main(count: int = 10, levels: dict | None = None,
         map_path: Path = DEFAULT_MAP) -> int:
    levels = levels or {f"{k}_level": "off" for k in gt._OPTION_KEYS}

    out_dir = project_path("data", "snaps") / gt.batch_tag(**levels)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i in range(count):
        seed = 42 + i
        frame, truth = gt.sample_frame(map_path, seed=seed, **levels)

        stem = f"frame_{i:04d}_seed{seed}"
        png = out_dir / f"{stem}.png"
        frame.save(png)
        (out_dir / f"{stem}.json").write_text(truth.to_json(), encoding="utf-8")

        est = mt.locate_frame(png, map_path)
        results.append(mt.record_result(png.name, est, truth))

    # ---- one archived file for the whole run ------------------------------
    run_file = mt.save_run(results, map_path=map_path)

    # ---- report -----------------------------------------------------------
    print(f"\nBATCH : {out_dir.name}")
    print(f"MAP   : {map_path.name}  ({results[0]['truth']['crop_size_px']}px crop, "
          f"{results[0]['truth']['resolution']} m/px)")
    print(f"VERDICT: inliers >= {mt.MIN_INLIERS} and within {mt.POS_TOL_M} m / "
          f"{mt.HEAD_TOL_DEG} deg -> correct | inliers < {mt.MIN_INLIERS} -> "
          f"unsure | else confident_wrong\n")
    print(f"{'frame':>22} {'truth_px':>16} {'truth_hdg':>10} "
          f"{'posErr_m':>9} {'headErr_deg':>12} {'inliers':>8}  verdict")
    print("-" * 96)

    for r in results:
        t = r["truth"]
        tpx = f"({t['px_x']},{t['px_y']})"
        pe = "--" if r["position_error_m"] is None else f"{r['position_error_m']:.2f}"
        he = "--" if r["heading_error_deg"] is None else f"{r['heading_error_deg']:.2f}"
        print(f"{r['frame']:>22} {tpx:>16} {t['heading_deg']:10.2f} "
              f"{pe:>9} {he:>12} {r['inliers']:>8}  {r['verdict']}")

    s = {
        "total": len(results),
        "correct": sum(1 for r in results if r["verdict"] == "correct"),
        "unsure": sum(1 for r in results if r["verdict"] == "unsure"),
        "confident_wrong": sum(1 for r in results
                               if r["verdict"] == "confident_wrong"),
    }
    pos = [r["position_error_m"] for r in results if r["position_error_m"] is not None]
    hdg = [abs(r["heading_error_deg"]) for r in results
           if r["heading_error_deg"] is not None]
    inl = [r["inliers"] for r in results if r["estimate"] is not None]

    print("-" * 96)
    if pos:
        print(f"median posErr={sorted(pos)[len(pos)//2]:.2f} m   "
              f"median |headErr|={sorted(hdg)[len(hdg)//2]:.2f} deg   "
              f"median inliers={sorted(inl)[len(inl)//2]}")
    print(f"correct={s['correct']}  unsure={s['unsure']}  "
          f"confident_wrong={s['confident_wrong']}  total={s['total']}")
    print(f"run archived -> {run_file}")

    return 0 if s["correct"] == s["total"] else 1


if __name__ == "__main__":
    opts, positionals = gt.parse_args(sys.argv)
    map_path = resolve_path(positionals[0]) if positionals else DEFAULT_MAP
    levels = {f"{k}_level": opts[k] for k in gt._OPTION_KEYS}
    raise SystemExit(main(int(opts["count"]), levels, map_path))
