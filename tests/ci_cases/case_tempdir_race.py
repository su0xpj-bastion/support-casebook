import os
import tempfile


def test_cleanup_while_a_child_still_writes():
    # shutil.rmtree loses the race the same way: a file appears after the directory was emptied.
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "late.log"), "w") as fh:
        fh.write("written by a process that outlived the test")
    os.rmdir(d)
