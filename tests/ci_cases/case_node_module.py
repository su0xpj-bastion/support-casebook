import subprocess


def test_node_package_missing():
    p = subprocess.run(["node", "-e", "require('casebook-not-installed')"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
