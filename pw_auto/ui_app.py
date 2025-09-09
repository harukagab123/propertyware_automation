# pw_auto/ui_app.py — Streamlit UI
from pathlib import Path
import sys, subprocess, threading, queue, os
import streamlit as st

st.set_page_config(page_title="Propertyware Automation", layout="wide")
st.title("Propertyware Automation")
st.caption("Run steps 1–6 with one click. Live logs below.")

REPO_ROOT = Path(__file__).resolve().parents[1]
st.write({
    "repo_root": str(REPO_ROOT),
    "cwd": os.getcwd(),
    "python": sys.executable,
    "ui_file": __file__,
})

orc_path = REPO_ROOT / "pw_auto" / "orchestrator.py"
if not orc_path.exists():
    st.error(f"Missing orchestrator at: {orc_path}")
    st.stop()

steps = st.multiselect(
    "Select steps to run",
    options=list(range(1, 7)),
    default=list(range(1, 7)),
    format_func=lambda i: f"Step {i}",
)
continue_on_error = st.checkbox("Continue on error", value=False)

def compress_steps(seq):
    if not seq: return ""
    s = sorted(set(seq))
    out, start, prev = [], s[0], s[0]
    for x in s[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = x
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(out)

step_str = compress_steps(steps)

col1, col2 = st.columns([1, 1])
run_clicked = col1.button("▶ Run", type="primary", use_container_width=True)
install_clicked = col2.button("Install Playwright (Edge)", use_container_width=True)

log_area = st.empty()

def stream_cmd(args, cwd=None):
    q = queue.Queue()
    def worker():
        try:
            proc = subprocess.Popen(
                args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
        except Exception as e:
            q.put(f"[launcher error] {e}")
            q.put(None)
            return
        try:
            for line in proc.stdout:
                q.put(line.rstrip("\n"))
        finally:
            proc.wait()
            q.put(f"[exit code] {proc.returncode}")
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    lines = []
    while True:
        item = q.get()
        if item is None:
            break
        lines.append(item)
        log_area.code("\n".join(lines[-600:]), language="bash")
    return "\n".join(lines)

if install_clicked:
    with st.spinner("Installing Playwright browsers (msedge)…"):
        out = stream_cmd([sys.executable, "-m", "playwright", "install", "msedge"], cwd=str(REPO_ROOT))
    st.success("Playwright install finished. See logs above.")

if run_clicked:
    if not steps:
        st.warning("Pick at least one step.")
    else:
        cmd = [sys.executable, "-m", "pw_auto.orchestrator"]
        if step_str:
            cmd += ["--steps", step_str]
        if continue_on_error:
            cmd += ["--continue-on-error"]
        st.info("Running: " + " ".join(cmd))
        with st.spinner("Working…"):
            _ = stream_cmd(cmd, cwd=str(REPO_ROOT))
        st.success("Finished. Check logs above.")
