"""Gate a candidate's eval runs against the baseline and write verdict.json.

Usage (run_eval.sh calls this): python scripts/eval/gate.py --candidate <sha> [--baseline] [--target M]

Pass requires all of:
  * code metrics (summed over runs) no worse than the baseline, within small tolerances;
  * the Argon judge's rubric scores no worse than the baseline's (mean, -0.5 allowed);
  * blind A/B (randomized order) does not prefer the baseline more often than the candidate;
  * --target metric, if given, strictly better.
Counts always come from code (metrics.json); the judge only grades quality.
"""

import os
import sys
import json
import random
import shutil
import argparse
import statistics
import subprocess
import concurrent.futures
from pathlib import Path


HERE = Path(__file__).parent
EVAL_DIR = Path(os.path.expanduser(os.environ.get("REACHY_EVAL_DIR", "~/.reachy_eval")))
JUDGE_MODEL = os.environ.get("REACHY_EVAL_JUDGE", "gemini-4-argon-high")
AGY = os.environ.get("AG_BIN") or shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")

# metric, +1 if higher is better / -1 if lower is better, slack (summed over runs)
COUNT_CHECKS = [
    ("heard", +1, 1),
    ("replied", +1, 1),
    ("names_recalled", +1, 1),
    ("sleep_reached", +1, 0),
    ("unprompted_speech", -1, 1),
    ("placeholders_shown", -1, 0),
    ("tool_echoes_shown", -1, 0),
    ("restarts", -1, 0),
    ("errors", -1, 0),
    ("mcp_writes_blocked", -1, 0),
]
RUBRIC_DIMS = ["responsiveness", "restraint", "memory", "conversation", "task_success"]
RUBRIC_SLACK = 0.5


def _runs(sha: str) -> list[Path]:
    root = EVAL_DIR / "results" / sha
    return sorted(p for p in root.glob("run*") if (p / "metrics.json").exists())


def _agy_json(prompt: str) -> dict:
    """Ask the judge; return the JSON object it replied with ({} on failure)."""
    try:
        out = subprocess.run(
            [AGY, "-p", prompt, "--model", JUDGE_MODEL], capture_output=True, text=True, timeout=280
        ).stdout
        return json.loads(out[out.index("{") : out.rindex("}") + 1])
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _rubric(transcript: Path) -> dict:
    return _agy_json(
        f"Read the rubric at {HERE / 'rubric.md'} and the transcript at {transcript}. "
        "Grade the transcript exactly as the rubric says and reply with only the JSON."
    )


def _ab(a: Path, b: Path) -> dict:
    return _agy_json(
        f"Read the rubric at {HERE / 'rubric.md'}, the comparison instructions at {HERE / 'rubric_ab.md'}, "
        f"transcript A at {a} and transcript B at {b}. Reply with only the JSON the comparison asks for."
    )


def rubric_scores(sha: str) -> list[dict]:
    """Judge each run once and cache the scores next to the runs."""
    cache = EVAL_DIR / "results" / sha / "judge_rubric.json"
    if cache.exists():
        return json.loads(cache.read_text())
    runs = _runs(sha)
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        scores = list(pool.map(lambda r: _rubric(r / "transcript.txt"), runs))
    if all("error" not in s for s in scores):
        cache.write_text(json.dumps(scores, indent=2))
    return scores


def blind_ab(cand: str, base: str) -> list[dict]:
    """Pair run i of each, shuffle which one is A, and map the verdict back."""
    pairs = list(zip(_runs(cand), _runs(base)))
    rng = random.Random()
    tmp = EVAL_DIR / "results" / cand / "ab"
    tmp.mkdir(exist_ok=True)
    jobs = []
    for i, (c, b) in enumerate(pairs):
        cand_is_a = rng.random() < 0.5
        a_src, b_src = (c, b) if cand_is_a else (b, c)
        a_path, b_path = tmp / f"pair{i}-A.txt", tmp / f"pair{i}-B.txt"
        shutil.copy(a_src / "transcript.txt", a_path)
        shutil.copy(b_src / "transcript.txt", b_path)
        jobs.append((cand_is_a, a_path, b_path))
    with concurrent.futures.ThreadPoolExecutor(max(1, len(jobs))) as pool:
        verdicts = list(pool.map(lambda j: _ab(j[1], j[2]), jobs))
    out = []
    for (cand_is_a, _, _), v in zip(jobs, verdicts):
        better = str(v.get("better", "")).strip().upper()
        winner = "tie" if better == "TIE" else ("error" if better not in ("A", "B") else None)
        if winner is None:
            winner = "candidate" if (better == "A") == cand_is_a else "baseline"
        out.append({"winner": winner, "reason": v.get("reason") or v.get("error", "")})
    return out


def totals(sha: str) -> dict:
    """Sum count metrics over runs; pool latencies."""
    ms = [json.loads((r / "metrics.json").read_text()) for r in _runs(sha)]
    t = {name: int(sum(int(m[name]) for m in ms)) for name, _, _ in COUNT_CHECKS}
    lat = [x["latency_s"] for m in ms for x in m["turns"] if x["latency_s"] is not None]
    t["median_latency_s"] = round(statistics.median(lat), 2) if lat else None
    t["runs"] = len(ms)
    return t


def rubric_means(scores: list[dict]) -> dict:
    """Mean of each rubric dimension over runs that were graded."""
    good = [s for s in scores if all(isinstance(s.get(d), (int, float)) for d in RUBRIC_DIMS)]
    return {d: round(statistics.mean(s[d] for s in good), 2) for d in RUBRIC_DIMS} if good else {}


def main() -> int:
    """Compute the verdict, write it, print a summary, exit 0 on pass."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--baseline", action="store_true", help="record the candidate as the new baseline")
    ap.add_argument("--target", help="metric that must strictly improve")
    ap.add_argument(
        "--against", help="compare with this evaluated commit instead of the baseline (chained candidates)"
    )
    args = ap.parse_args()

    cand = args.candidate
    cand_dir = EVAL_DIR / "results" / cand
    harness_id = (cand_dir / "harness_id").read_text().strip()
    cand_totals = totals(cand)
    cand_scores = rubric_scores(cand)
    verdict: dict = {
        "candidate": cand,
        "harness_id": harness_id,
        "judge": JUDGE_MODEL,
        "totals": cand_totals,
        "rubric": rubric_means(cand_scores),
        "issues": sorted({i for s in cand_scores for i in s.get("issues", [])}),
    }

    if args.baseline:
        verdict.update({"pass": True, "note": "recorded as baseline"})
        (EVAL_DIR / "baseline").write_text(cand + "\n")
    else:
        base_file = EVAL_DIR / "baseline"
        base = base_file.read_text().strip() if base_file.exists() else ""
        checks: list[dict] = []
        if args.against:
            base = subprocess.run(
                ["git", "rev-parse", args.against], capture_output=True, text=True, check=True
            ).stdout.strip()
            prior = EVAL_DIR / "results" / base / "verdict.json"
            if not prior.exists() or not json.loads(prior.read_text()).get("pass"):
                checks.append({"check": "comparison commit passed its gate", "ok": False, "detail": base[:8]})
        if not base or not _runs(base):
            checks.append({"check": "baseline exists", "ok": False, "detail": "run with --baseline first"})
        elif (EVAL_DIR / "results" / base / "harness_id").read_text().strip() != harness_id:
            checks.append(
                {"check": "same harness as baseline", "ok": False, "detail": f"re-run the baseline {base[:8]}"}
            )
        else:
            base_totals = totals(base)
            verdict["baseline"] = base
            verdict["baseline_totals"] = base_totals
            if base_totals["runs"] != cand_totals["runs"]:
                checks.append({"check": "same number of runs", "ok": False})
            for name, sign, slack in COUNT_CHECKS:
                b, c = base_totals[name], cand_totals[name]
                checks.append({"check": name, "baseline": b, "candidate": c, "ok": sign * (c - b) >= -slack})
            bl, cl = base_totals["median_latency_s"], cand_totals["median_latency_s"]
            if bl is not None and cl is not None:
                checks.append(
                    {"check": "median_latency_s", "baseline": bl, "candidate": cl, "ok": cl <= bl * 1.25 + 0.5}
                )
            base_means, cand_means = rubric_means(rubric_scores(base)), verdict["rubric"]
            verdict["baseline_rubric"] = base_means
            if not base_means or not cand_means:
                checks.append({"check": "judge rubric", "ok": False, "detail": "judge failed — re-run gate.py"})
            for d in RUBRIC_DIMS if base_means and cand_means else []:
                checks.append(
                    {
                        "check": f"judge {d}",
                        "baseline": base_means[d],
                        "candidate": cand_means[d],
                        "ok": cand_means[d] >= base_means[d] - RUBRIC_SLACK,
                    }
                )
            ab = blind_ab(cand, base)
            verdict["ab"] = ab
            wins = {w: sum(1 for x in ab if x["winner"] == w) for w in ("candidate", "baseline", "tie", "error")}
            checks.append(
                {
                    "check": "blind A/B",
                    "detail": wins,
                    "ok": wins["error"] == 0 and wins["baseline"] <= wins["candidate"],
                }
            )
            if args.target:
                t = args.target
                if t in base_totals:
                    sign = next(s for n, s, _ in COUNT_CHECKS if n == t) if t != "median_latency_s" else -1
                    b, c = base_totals[t], cand_totals[t]
                elif t in RUBRIC_DIMS and base_means and cand_means:
                    sign, b, c = +1, base_means[t], cand_means[t]
                else:
                    sign, b, c = 0, None, None
                ok = b is not None and c is not None and sign * (c - b) > 0
                checks.append({"check": f"target {t} improves", "baseline": b, "candidate": c, "ok": ok})
        verdict["checks"] = checks
        verdict["pass"] = all(x["ok"] for x in checks)

    (cand_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
    print(f"\ncandidate {cand[:8]}  totals over {cand_totals['runs']} runs: {cand_totals}")
    print(f"judge rubric: {verdict['rubric']}")
    for x in verdict.get("checks", []):
        mark = "✓" if x["ok"] else "✗"
        rest = f"{x.get('baseline')} → {x.get('candidate')}" if "baseline" in x else x.get("detail", "")
        print(f"  {mark} {x['check']}: {rest}")
    print(("PASS" if verdict["pass"] else "FAIL") + f" — {cand_dir / 'verdict.json'}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
