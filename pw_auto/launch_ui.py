# pw_auto/launch_ui.py
import sys
import subprocess
from pathlib import Path

def main():
    # resolve the actual file path of the Streamlit app
    import pw_auto.ui_app as ui_app
    ui_path = Path(ui_app.__file__).resolve()
    # run: python -m streamlit run <file>
    cmd = [sys.executable, "-m", "streamlit", "run", str(ui_path)]
    subprocess.run(cmd, check=False)
