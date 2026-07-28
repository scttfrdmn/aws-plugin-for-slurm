"""The plugin must run on the Python version it documents.

README and docs/upgrade-guide.md both promise Python 3.6+, which matters because the
on-premises headnodes this plugin targets are commonly RHEL/CentOS 7 (system Python
3.6). CI cannot install 3.6 on current GitHub runners, so the floor is enforced here by
scanning for constructs newer than it.

This catches syntax and the specific stdlib arguments that have bitten this codebase.
It is not a full 3.6 compatibility checker — a genuinely new stdlib function would slip
through. If the floor is ever raised deliberately, update MINIMUM_VERSION and the docs
together.
"""

import ast
import os
import unittest

MINIMUM_VERSION = (3, 6)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLUGIN_FILES = (
    'common.py', 'resume.py', 'suspend.py', 'change_state.py',
    'generate_conf.py', 'health_check.py',
)

# Keyword arguments added after the floor, and what to use instead.
FORBIDDEN_KEYWORDS = {
    'capture_output': (
        (3, 7), 'subprocess.run(capture_output=True)',
        'pass stdout=subprocess.PIPE, stderr=subprocess.PIPE'),
    'text': (
        (3, 7), 'subprocess.run(text=True)',
        'pass universal_newlines=True'),
}


def _plugin_sources():
    for name in PLUGIN_FILES:
        path = os.path.join(REPO_ROOT, name)
        with open(path) as f:
            yield name, f.read()


def _test_sources():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(tests_dir)):
        if name.endswith('.py'):
            with open(os.path.join(tests_dir, name)) as f:
                yield os.path.join('tests', name), f.read()


class TestSyntaxFloor(unittest.TestCase):

    def test_no_syntax_newer_than_the_floor(self):
        newer_nodes = {}
        if hasattr(ast, 'NamedExpr'):
            newer_nodes[ast.NamedExpr] = ('walrus operator :=', (3, 8))
        if hasattr(ast, 'Match'):
            newer_nodes[ast.Match] = ('match statement', (3, 10))

        for name, source in _plugin_sources():
            tree = ast.parse(source, name)
            for node in ast.walk(tree):
                for node_type, (label, version) in newer_nodes.items():
                    if isinstance(node, node_type):
                        self.fail('%s:%d uses the %s (Python %d.%d+), but the '
                                  'documented floor is %d.%d'
                                  % (name, node.lineno, label, version[0], version[1],
                                     MINIMUM_VERSION[0], MINIMUM_VERSION[1]))
                if isinstance(node, ast.arguments) and getattr(node, 'posonlyargs', []):
                    self.fail('%s uses positional-only parameters (Python 3.8+)' % name)

    def test_no_stdlib_keywords_newer_than_the_floor(self):
        for name, source in _plugin_sources():
            tree = ast.parse(source, name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg in FORBIDDEN_KEYWORDS:
                        version, what, instead = FORBIDDEN_KEYWORDS[keyword.arg]
                        self.fail(
                            '%s:%d uses %s, which is Python %d.%d+. The documented '
                            'floor is %d.%d - %s.'
                            % (name, node.lineno, what, version[0], version[1],
                               MINIMUM_VERSION[0], MINIMUM_VERSION[1], instead))

    def test_every_plugin_file_was_scanned(self):
        # Guard against a renamed file silently dropping out of coverage.
        for name in PLUGIN_FILES:
            self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, name)),
                            '%s is listed but missing' % name)

    def test_no_plugin_file_is_missing_from_the_list(self):
        on_disk = {f for f in os.listdir(REPO_ROOT) if f.endswith('.py')}
        self.assertEqual(on_disk - set(PLUGIN_FILES), set(),
                         'new plugin script not covered by the version floor scan')


class TestTestSuiteFloor(unittest.TestCase):
    """The suite must run on the Python the plugin supports.

    Otherwise an operator on the oldest supported headnode can install the plugin but
    cannot run its tests, which is exactly when they would most want to.
    """

    def test_the_suite_itself_respects_the_floor(self):
        for name, source in _test_sources():
            tree = ast.parse(source, name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # This file names the forbidden keywords in data, not in calls.
                if name.endswith('test_python_floor.py'):
                    continue
                for keyword in node.keywords:
                    if keyword.arg in FORBIDDEN_KEYWORDS:
                        version, what, instead = FORBIDDEN_KEYWORDS[keyword.arg]
                        self.fail(
                            '%s:%d uses %s, which is Python %d.%d+. The suite must run '
                            'on the documented floor of %d.%d - %s.'
                            % (name, node.lineno, what, version[0], version[1],
                               MINIMUM_VERSION[0], MINIMUM_VERSION[1], instead))


class TestDocumentedFloorMatches(unittest.TestCase):
    """The floor asserted here must be the one the docs promise."""

    def test_readme_states_the_same_floor(self):
        with open(os.path.join(REPO_ROOT, 'README.md')) as f:
            readme = f.read()
        expected = 'Python %d.%d+' % MINIMUM_VERSION
        self.assertIn(expected, readme,
                      'README should document %s to match MINIMUM_VERSION' % expected)


if __name__ == '__main__':
    unittest.main()
