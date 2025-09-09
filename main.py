# main.py — orchestrate step1..step6
import sys
import subprocess
import time
from pathlib import Path
import argparse

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_STEPS = {
    1: ["step1_login.py"],
    2: ["step2_headers.py"],
    3: ["step3_rows_sample.py", "step3_row_sample.py"],  # tries either name
    4: ["step4_opening_url.py"],
    5: ["step5_other_details.py"],
    6: ["step6_generate.py"],
}

def find_script(candidates):
    """Return the first existing script in candidates (relative to project root), or the first name anyway."""
    for name in candidates:
        p = PROJECT_ROOT / name
        if p.exists():
            return p
    return PROJECT_ROOT / candidates[0]

def run_step(py_exe, script_path, extra_args=None):
    """Run a step script and stream output. Return (returncode, elapsed_sec)."""
    cmd = [str(py_exe), "-u", str(script_path)]
    if extra_args:
        cmd.extend(extra_args)

    print(f"\n=== RUN {script_path.name} ===")
    print(" ".join(cmd))
    start = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    # stream output
    try:
        for line in proc.stdout:
            print(line, end="")
    finally:
        proc.wait()
    elapsed = time.time() - start
    print(f"=== DONE {script_path.name} (rc={proc.returncode}, {elapsed:.1f}s) ===\n")
    return proc.returncode, elapsed

def parse_steps_arg(arg: str):
    """
    Parse step ranges like "1-6", "1,3,5-6".
    Returns a sorted list of unique ints within 1..6.
    """
    wanted = set()
    for part in arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            for x in range(min(lo, hi), max(lo, hi) + 1):
                if 1 <= x <= 6:
                    wanted.add(x)
        else:
            try:
                x = int(part)
                if 1 <= x <= 6:
                    wanted.add(x)
            except ValueError:
                pass
    return sorted(wanted) if wanted else list(range(1, 7))

def ensure_dirs():
    # make common output dirs so steps don't fail on missing paths
    for rel in ["data/debug", "data/notices", "templates"]:
        (PROJECT_ROOT / rel).mkdir(parents=True, exist_ok=True)

def main():
    ap = argparse.ArgumentParser(description="Run Propertyware automation steps 1..6")
    ap.add_argument("--steps", default="1-6", help='Steps to run, e.g. "1-6" or "1,3,5-6" (default: 1-6)')
    ap.add_argument("--continue-on-error", action="store_true", help="Do not stop on first failing step")
    ap.add_argument("--python", default=sys.executable, help="Python executable to use (default: current)")
    args = ap.parse_args()

    steps_to_run = parse_steps_arg(args.steps)
    py_exe = Path(args.python)

    ensure_dirs()

    total_elapsed = 0.0
    any_fail = False

    print(f"Using Python: {py_exe}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Planned steps: {steps_to_run}\n")

    for step_no in steps_to_run:
        script = find_script(DEFAULT_STEPS[step_no])
        if not script.exists():
            print(f"!! Missing script for step {step_no}: {script.name}")
            any_fail = True
            if not args.continue_on_error:
                break
            else:
                continue

        rc, elapsed = run_step(py_exe, script)
        total_elapsed += elapsed
        if rc != 0:
            any_fail = True
            print(f"!! Step {step_no} failed with return code {rc}.")
            if not args.continue_on_error:
                print("Stopping due to error. (Use --continue-on-error to keep going.)")
                break

    print("\n========== SUMMARY ==========")
    print(f"Steps run      : {steps_to_run}")
    print(f"Total time     : {total_elapsed:.1f}s")
    print(f"Overall status : {'OK' if not any_fail else 'WITH ERRORS'}")
    print("================================\n")

    sys.exit(1 if any_fail else 0)

if __name__ == "__main__":
    main()
