import os

from ladon.magic import SharedSession


class DummyProc:
    def __init__(self, rfile):
        self.stdout = rfile
        self.stdin = None


def test_drain_stdout_consumes_pipe():
    # Create a pipe with some data and ensure _drain_stdout does not block
    rfd, wfd = os.pipe()
    rfile = os.fdopen(rfd, "r", buffering=1)
    wfile = os.fdopen(wfd, "w", buffering=1)
    wfile.write("line1\nline2\n")
    wfile.flush()
    wfile.close()

    sess = SharedSession.__new__(SharedSession)  # bypass __init__
    sess.proc = DummyProc(rfile)

    # Should return quickly and consume the pending lines
    sess._drain_stdout()

    # The reader should now be at EOF
    assert rfile.readline() == ""

    rfile.close()
