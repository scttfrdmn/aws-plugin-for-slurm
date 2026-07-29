"""Sandbox environment patches: the clock, TCP, and ping.

Copied into the sandbox as sitecustomize.py, which CPython imports automatically at
interpreter startup (the sandbox is on PYTHONPATH). That is early enough to patch the
stdlib before resume.py imports it, and it keeps these concerns out of the fake boto3,
which only models the AWS API.

Controlled by environment variables set by harness.Sandbox.run():

    FAKE_NO_SLEEP=1        time.sleep advances a fake clock instead of blocking
    FAKE_HEALTHY_IPS=all   every IP passes the slurmd port check and ping
    FAKE_HEALTHY_IPS=a,b   only these IPs pass; others are refused instantly

Without the network patch, a readiness check against a fake IP would sit in a real
TCP timeout for every poll of every instance.
"""

import os
import socket
import subprocess
import time


# -- clock -------------------------------------------------------------------
#
# resume.py's readiness wait sleeps 5s per poll and compares time.time() against a
# deadline. Advancing a fake offset keeps the timeout logic under test while running
# instantly.
if os.environ.get('FAKE_NO_SLEEP') == '1':
    _offset = [0.0]
    _real_time = time.time

    def _fake_sleep(seconds):
        _offset[0] += seconds

    def _fake_time():
        return _real_time() + _offset[0]

    time.sleep = _fake_sleep
    time.time = _fake_time


# -- network -----------------------------------------------------------------

def _healthy_ips():
    value = os.environ.get('FAKE_HEALTHY_IPS', '')
    if value == 'all':
        return 'all'
    return {ip.strip() for ip in value.split(',') if ip.strip()}


_HEALTHY = _healthy_ips()


def _is_healthy(ip_address):
    return _HEALTHY == 'all' or ip_address in _HEALTHY


class _FakeSocket:
    """Enough of socket.socket for resume.check_port()."""

    def __init__(self, *args, **kwargs):
        self._timeout = None

    def settimeout(self, timeout):
        self._timeout = timeout

    def connect(self, address):
        host = address[0]
        if not _is_healthy(host):
            # What a node whose slurmd is not up yet looks like.
            raise ConnectionRefusedError('fake: %s is not healthy' % host)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


_real_socket = socket.socket
_real_run = subprocess.run


def _fake_socket(*args, **kwargs):
    return _FakeSocket(*args, **kwargs)


def _fake_run(cmd, *args, **kwargs):
    """Intercept the ICMP health check; pass everything else through."""
    if isinstance(cmd, (list, tuple)) and cmd and os.path.basename(str(cmd[0])) == 'ping':
        target = str(cmd[-1])
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=0 if _is_healthy(target) else 1,
            stdout=b'', stderr=b'',
        )
    return _real_run(cmd, *args, **kwargs)


socket.socket = _fake_socket
subprocess.run = _fake_run
