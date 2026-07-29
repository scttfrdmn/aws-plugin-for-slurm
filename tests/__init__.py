"""Test package init.

Most tests run the plugin as a subprocess inside a sandbox, where a fake boto3 shadows
the real package on sys.path. But test_validate.py and test_node_names.py import
common.py directly to call its pure functions, and common.py does `import boto3` at
module scope. On a machine with boto3 installed that works by accident; on a bare CI
runner it is a ModuleNotFoundError before a single assertion runs.

Registering the fake in sys.modules here fixes that and, more importantly, makes the
result the same either way: no test can quietly start depending on the real boto3's
behavior just because the developer happens to have it installed.

Nothing here touches the network. If the real boto3 is present it is left alone for any
code that genuinely wants it — the fake is only a fallback.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _install_fake_boto3():
    if 'boto3' in sys.modules:
        return

    try:
        import boto3  # noqa: F401
    except ImportError:
        pass
    else:
        return

    if _TESTS_DIR not in sys.path:
        sys.path.insert(0, _TESTS_DIR)

    import fake_boto3

    sys.modules['boto3'] = fake_boto3


_install_fake_boto3()
