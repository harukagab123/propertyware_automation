# pw_auto/orchestrator.py
from __future__ import annotations
import sys, subprocess, argparse, re, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

STEP_SCRIPTS = {
    1: "step1_login.py",
    2: "step2_headers.py",
    3: "step3_row_sample.py",
    4: "step4_opening_url.py",
    5: "step5_other_details.py",
    6: "step6_generate.py",
}

def parse_steps(spec: str | None) -> list[int]:
    if not spec:
        return list(STEP_SCRIPTS.keys())
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\-(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b: a, b = b, a
            out.update(range(a, b+1))
        else:
            out.add(int(part))
    valid = [s for s in sorted(out) if s in STEP_SCRIPTS]
    return valid

def run_script(path: Path) -> int:
    if not path.exists():
        print(f"[orchestrator] MISSING: {path}")
        return 2
    print(f"[orchestrator] RUN: {path.name}")
    cmd = [sys.executable, "-u", str(path)]
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in proc.stdout:
            print(line, end="")
    finally:
        proc.wait()
    print(f"[orchestrator] EXIT {path.name}: {proc.returncode}\n")
    return proc.returncode

def cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Propertyware automation steps")
    ap.add_argument("--steps", help="E.g. 1-3,5,6 (default: all 1-6)")
    ap.add_argument("--continue-on-error", action="store_true", help="Do not stop if a step fails")
    args = ap.parse_args(argv)

    steps = parse_steps(args.steps)
    if not steps:
        print("[orchestrator] No valid steps selected.")
        return 1

    overall = 0
    for s in steps:
        script = REPO_ROOT / STEP_SCRIPTS[s]
        rc = run_script(script)
        if rc != 0:
            overall = rc
            if not args.continue_on_error:
                print(f"[orchestrator] Stopping at step {s} due to error.")
                break
        time.sleep(0.1)  # tiny gap for readability
    return overall

if __name__ == "__main__":
    raise SystemExit(cli())
