import subprocess
import sys


def test_child_hangs():
    subprocess.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)
