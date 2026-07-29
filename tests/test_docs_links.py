"""Internal documentation links must resolve.

This repo's docs cross-reference each other heavily, and broken links have accumulated
before: three anchors in docs/onprem-to-aws-bursting.md pointed at headings that had been
renamed or that lived in a different file. Nothing catches that by reading the diff.

Scope is deliberately internal-only. Relative file paths and same-document anchors are
checked; external http(s) links are not, because a CI job that fails when someone else's
site is down is a job people learn to ignore.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv'}

# [text](target) — target stops at whitespace or the closing paren, so it does not
# swallow a "(target 'title')" form. Images are matched too; a missing diagram is a
# broken link.
LINK_RE = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')

HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*#*$', re.MULTILINE)

FENCE_RE = re.compile(r'^\s*(```|~~~)')

# A scheme (http:, mailto:) or a protocol-relative URL is external.
EXTERNAL_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.-]*:|//)')


def _strip_code_fences(source):
    """Blank out fenced blocks so example markdown inside them is not checked.

    Lines are replaced rather than removed to keep line numbers meaningful in failures.
    """
    lines = source.split('\n')
    out = []
    fence = None
    for line in lines:
        match = FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)
            out.append('')
        elif fence is not None and match and match.group(1) == fence:
            fence = None
            out.append('')
        elif fence is not None:
            out.append('')
        else:
            out.append(line)
    return '\n'.join(out)


def _slugify(heading):
    """Approximate GitHub's heading-to-anchor rule.

    GitHub lowercases, drops anything that is not a word character, hyphen or space,
    then turns runs of spaces into hyphens. Inline markup is unwrapped first so
    `## Use `srun`` becomes use-srun.
    """
    text = heading.strip()
    text = re.sub(r'`([^`]*)`', r'\1', text)            # `code`
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)  # [text](url)
    text = re.sub(r'[*_]+', '', text)                    # **bold**, _italic_
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    return re.sub(r'\s+', '-', text.strip())


def _markdown_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in sorted(filenames):
            if filename.endswith('.md'):
                yield os.path.join(dirpath, filename)


_ANCHOR_CACHE = {}


def _anchors(path):
    """Every anchor a link could target in a markdown file."""
    if path in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[path]

    with open(path) as f:
        source = _strip_code_fences(f.read())

    anchors = set()
    for _, heading in HEADING_RE.findall(source):
        anchors.add(_slugify(heading))
        # GitHub disambiguates duplicate headings with -1, -2, ...; accept a couple so a
        # legitimate link to a repeated heading is not reported as broken.
        for n in range(1, 4):
            anchors.add('%s-%d' % (_slugify(heading), n))

    # Explicit anchors: <a name="x"> or <a id="x">.
    for attr_match in re.finditer(r'<a\s+[^>]*(?:name|id)\s*=\s*["\']([^"\']+)["\']',
                                  source, re.IGNORECASE):
        anchors.add(attr_match.group(1).lower())

    _ANCHOR_CACHE[path] = anchors
    return anchors


def _link_locations(path):
    """Yield (lineno, target) for every internal link in a markdown file."""
    with open(path) as f:
        source = _strip_code_fences(f.read())

    for lineno, line in enumerate(source.split('\n'), 1):
        for target in LINK_RE.findall(line):
            if EXTERNAL_RE.match(target):
                continue
            yield lineno, target


class TestInternalLinks(unittest.TestCase):

    def test_every_linked_file_exists(self):
        broken = []
        for path in _markdown_files():
            rel = os.path.relpath(path, REPO_ROOT)
            for lineno, target in _link_locations(path):
                file_part = target.split('#', 1)[0]
                if not file_part:
                    continue  # same-document anchor, checked below
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(path), file_part))
                if not os.path.exists(resolved):
                    broken.append('%s:%d -> %s' % (rel, lineno, target))

        self.assertEqual(broken, [], 'links to files that do not exist:\n  %s'
                         % '\n  '.join(broken))

    def test_every_linked_anchor_exists(self):
        broken = []
        for path in _markdown_files():
            rel = os.path.relpath(path, REPO_ROOT)
            for lineno, target in _link_locations(path):
                file_part, _, fragment = target.partition('#')
                if not fragment:
                    continue

                if file_part:
                    resolved = os.path.normpath(
                        os.path.join(os.path.dirname(path), file_part))
                    # A missing file is the other test's failure, not this one's.
                    if not os.path.exists(resolved) or not resolved.endswith('.md'):
                        continue
                else:
                    resolved = path

                if fragment.lower() not in _anchors(resolved):
                    broken.append('%s:%d -> %s' % (rel, lineno, target))

        self.assertEqual(broken, [], 'links to headings that do not exist:\n  %s'
                         % '\n  '.join(broken))

    def test_no_link_points_outside_the_repo(self):
        # A ../.. that escapes the repo works on the author's disk and nowhere else.
        escaping = []
        for path in _markdown_files():
            rel = os.path.relpath(path, REPO_ROOT)
            for lineno, target in _link_locations(path):
                file_part = target.split('#', 1)[0]
                if not file_part:
                    continue
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(path), file_part))
                if not resolved.startswith(REPO_ROOT + os.sep):
                    escaping.append('%s:%d -> %s' % (rel, lineno, target))

        self.assertEqual(escaping, [], 'links resolving outside the repo:\n  %s'
                         % '\n  '.join(escaping))


class TestTheCheckerWorks(unittest.TestCase):
    """The checker is only worth having if it can actually fail."""

    def test_it_finds_markdown_to_check(self):
        # A typo in SKIP_DIRS or the walk would silently check nothing and pass.
        files = list(_markdown_files())
        self.assertGreater(len(files), 5, 'found almost no markdown; the walk is broken')
        self.assertIn(os.path.join(REPO_ROOT, 'README.md'), files)

    def test_it_finds_links_to_check(self):
        readme = os.path.join(REPO_ROOT, 'README.md')
        targets = [t for _, t in _link_locations(readme)]
        self.assertGreater(len(targets), 5, 'no internal links found in README')

    def test_slugify_matches_github_rules(self):
        cases = {
            'Multi-Region Deployments': 'multi-region-deployments',
            'GPU Configuration': 'gpu-configuration',
            'EFA (Elastic Fabric Adapter)': 'efa-elastic-fabric-adapter',
            'Use `srun` directly': 'use-srun-directly',
            '**Bold** heading': 'bold-heading',
            "What's next?": 'whats-next',
        }
        for heading, expected in cases.items():
            with self.subTest(heading=heading):
                self.assertEqual(_slugify(heading), expected)

    def test_code_fences_are_ignored(self):
        source = 'real [a](b.md)\n```\nfake [c](nope.md)\n```\n'
        stripped = _strip_code_fences(source)
        self.assertIn('b.md', stripped)
        self.assertNotIn('nope.md', stripped)


if __name__ == '__main__':
    unittest.main()
