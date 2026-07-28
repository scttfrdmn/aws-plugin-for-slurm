"""The suite must not need boto3 installed.

This was a real CI failure: test_validate.py and test_node_names.py import common.py to
call its pure functions, and common.py does `import boto3` at module scope. On a developer
machine with boto3 installed the suite passed; on a bare runner it was a
ModuleNotFoundError before any assertion ran. tests/__init__.py now registers the fake as
a fallback.

The risk is not just a red build. If the real boto3 satisfies that import locally, a test
can quietly come to depend on real boto3 behavior — and then the suite is not the hermetic
thing tests/README.md claims it is.
"""

import os
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)


def _run_without_boto3(code):
    """Run code in a subprocess where importing the real boto3 is impossible.

    A sitecustomize-style blocker is not enough because the real boto3 may already be
    importable from site-packages; instead a meta_path finder refuses it outright, which
    is what a runner without the package installed looks like.
    """
    preamble = (
        'import sys\n'
        'class _Blocker:\n'
        '    def find_module(self, name, path=None):\n'
        '        return self if name == "boto3" or name.startswith("boto3.") else None\n'
        '    def find_spec(self, name, path=None, target=None):\n'
        '        if name == "boto3" or name.startswith("boto3."):\n'
        '            raise ImportError("No module named %r (blocked by test)" % name)\n'
        '        return None\n'
        'sys.meta_path.insert(0, _Blocker())\n'
        'sys.modules.pop("boto3", None)\n'
    )
    return subprocess.run(
        [sys.executable, '-c', preamble + code],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=120,
    )


class TestSuiteRunsWithoutBoto3(unittest.TestCase):

    def test_the_blocker_actually_blocks(self):
        # If this passes trivially, the tests below prove nothing.
        proc = _run_without_boto3('import boto3\nprint("imported")\n')
        self.assertNotEqual(proc.returncode, 0,
                            'the blocker did not block boto3, so this suite is vacuous')
        self.assertIn('No module named', proc.stderr)

    def test_common_is_importable_without_boto3(self):
        proc = _run_without_boto3(
            'import tests\n'          # installs the fake boto3 fallback
            'import common\n'
            'common.validate_config\n'
            'print("ok")\n'
        )
        self.assertEqual(proc.returncode, 0,
                         'importing common without boto3 failed:\n%s' % proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_in_process_modules_import_without_boto3(self):
        # The two modules that import common directly, rather than via a subprocess.
        for module in ('tests.test_validate', 'tests.test_node_names'):
            with self.subTest(module=module):
                proc = _run_without_boto3(
                    'import importlib\n'
                    'importlib.import_module(%r)\n'
                    'print("ok")\n' % module
                )
                self.assertEqual(
                    proc.returncode, 0,
                    '%s cannot be imported without boto3:\n%s' % (module, proc.stderr))

    def test_validate_suite_passes_without_boto3(self):
        proc = _run_without_boto3(
            'import unittest, sys\n'
            'suite = unittest.TestLoader().loadTestsFromNames(\n'
            '    ["tests.test_validate", "tests.test_node_names"])\n'
            'result = unittest.TextTestRunner(verbosity=0).run(suite)\n'
            'sys.exit(0 if result.wasSuccessful() else 1)\n'
        )
        self.assertEqual(proc.returncode, 0,
                         'validation tests fail without boto3:\n%s' % proc.stderr)

    def test_plugin_declares_no_third_party_imports_beyond_boto3(self):
        # boto3 is the plugin's only runtime dependency; a new one would need to be
        # documented in README and installed on every headnode.
        allowed = {'boto3', 'botocore'}
        stdlib_ok = set(sys.builtin_module_names) | {
            'argparse', 'json', 'logging', 'os', 're', 'socket', 'subprocess', 'sys',
            'time', 'datetime', 'collections', 'math', 'shutil', 'tempfile', 'glob',
            'ipaddress', 'random', 'string', 'traceback', 'copy',
        }

        import ast
        unexpected = []
        for name in ('common.py', 'resume.py', 'suspend.py', 'change_state.py',
                     'generate_conf.py', 'health_check.py'):
            with open(os.path.join(REPO_ROOT, name)) as f:
                tree = ast.parse(f.read(), name)
            for node in ast.walk(tree):
                roots = []
                if isinstance(node, ast.Import):
                    roots = [a.name.split('.')[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split('.')[0]]
                for root in roots:
                    if root in allowed or root in stdlib_ok:
                        continue
                    if root in ('common',):  # first-party
                        continue
                    unexpected.append('%s:%d imports %s' % (name, node.lineno, root))

        self.assertEqual(unexpected, [],
                         'new third-party dependency:\n  %s' % '\n  '.join(unexpected))


if __name__ == '__main__':
    unittest.main()
