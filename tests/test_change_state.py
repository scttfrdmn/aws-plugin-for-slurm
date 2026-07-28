"""change_state.py state transitions.

The stub scontrol serves canned 'scontrol show node' output, so each test is a node in
a given compound state and an assertion about the transitions the cron job requests.

The power-save tests are regressions: the code tested list membership for a bare
'POWER', but Slurm only ever emits POWERED_DOWN / POWERING_UP / POWER_DOWN /
POWERING_DOWN. Every power-save rule silently never fired.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Sandbox, base_nodegroup, base_partitions  # noqa: E402


def show_node_line(node_name, state, **extra):
    """One line of 'scontrol show node -o' output.

    Only the fields change_state.py parses are included; it splits on spaces and keeps
    tokens containing '='.
    """
    fields = ['NodeName=%s' % node_name, 'State=%s' % state, 'CPUTot=2']
    fields += ['%s=%s' % item for item in extra.items()]
    return ' '.join(fields)


class ChangeStateTestCase(unittest.TestCase):

    def setUp(self):
        self.sandboxes = []

    def tearDown(self):
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def run_with_nodes(self, *lines):
        """Run change_state.py against the given 'show node' lines."""
        sandbox = Sandbox(partitions=base_partitions(base_nodegroup(MaxNodes=4)))
        self.sandboxes.append(sandbox)
        sandbox.write_file('scontrol_show_node', '\n'.join(lines) + '\n')
        sandbox.run('change_state.py', expect_returncode=0)
        return sandbox

    def assertStates(self, sandbox, expected):
        self.assertEqual(sorted(sandbox.state_changes()), sorted(expected))


class TestPowerSaveStates(ChangeStateTestCase):
    """A node that is DOWN while powered down should be reset to IDLE, not powered
    down again. Getting this wrong left nodes permanently unavailable after a
    ResumeTimeout."""

    def test_down_and_powered_down_becomes_idle(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD+POWERED_DOWN'))
        self.assertIn(('aws-compute-0', 'IDLE'), sandbox.state_changes())
        self.assertNotIn(('aws-compute-0', 'POWER_DOWN'), sandbox.state_changes(),
                         'a powered-down node must not be powered down again')

    def test_down_and_powering_up_becomes_idle(self):
        # POWERING_UP is the state during a resume; 'POWER' in it, so power-save rules
        # apply.
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD+POWERING_UP'))
        self.assertIn(('aws-compute-0', 'IDLE'), sandbox.state_changes())

    def test_down_and_power_down_becomes_idle(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD+POWER_DOWN'))
        self.assertIn(('aws-compute-0', 'IDLE'), sandbox.state_changes())

    def test_down_and_powering_down_becomes_idle(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD+POWERING_DOWN'))
        self.assertIn(('aws-compute-0', 'IDLE'), sandbox.state_changes())

    def test_down_while_still_up_is_powered_down(self):
        # No power-save token: the instance is believed to be running, so reclaim it.
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD'))
        self.assertIn(('aws-compute-0', 'POWER_DOWN'), sandbox.state_changes())
        self.assertNotIn(('aws-compute-0', 'IDLE'), sandbox.state_changes())

    def test_drain_in_power_save_is_undrained(self):
        # This rule never ran at all before the fix.
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'IDLE+CLOUD+DRAIN+POWERED_DOWN'))
        self.assertIn(('aws-compute-0', 'UNDRAIN'), sandbox.state_changes())

    def test_drain_while_up_is_not_undrained(self):
        # A drained running node was drained deliberately; leave it alone.
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'IDLE+CLOUD+DRAIN'))
        self.assertNotIn(('aws-compute-0', 'UNDRAIN'), sandbox.state_changes())

    def test_bare_power_token_is_not_required(self):
        # Guard against a regression to `'POWER' in node_states`: no real Slurm state
        # is the bare string POWER, so such a test can only ever match nothing.
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD+POWERED_DOWN'))
        self.assertTrue(sandbox.state_changes(),
                        'a DOWN+POWERED_DOWN node must trigger some transition')


class TestUnresponsiveStates(ChangeStateTestCase):

    def test_down_star_is_powered_down(self):
        # The '*' suffix means slurmctld cannot reach slurmd.
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN*+CLOUD'))
        self.assertIn(('aws-compute-0', 'POWER_DOWN'), sandbox.state_changes())

    def test_idle_star_is_powered_down(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'IDLE*+CLOUD'))
        self.assertIn(('aws-compute-0', 'POWER_DOWN'), sandbox.state_changes())

    def test_reason_is_included(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'IDLE*+CLOUD'))
        self.assertTrue(
            any('reason=node_not_responding' in call for call in sandbox.scontrol_calls()),
            sandbox.scontrol_calls())

    def test_completing_and_drain_is_forced_down(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'COMPLETING+DRAIN+CLOUD'))
        self.assertIn(('aws-compute-0', 'DOWN'), sandbox.state_changes())

    def test_healthy_idle_node_is_left_alone(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'IDLE+CLOUD+POWERED_DOWN'))
        self.assertEqual(sandbox.state_changes(), [])

    def test_allocated_node_is_left_alone(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'ALLOCATED+CLOUD'))
        self.assertEqual(sandbox.state_changes(), [])

    def test_mixed_node_is_left_alone(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'MIXED+CLOUD'))
        self.assertEqual(sandbox.state_changes(), [])


class TestMultipleNodes(ChangeStateTestCase):

    def test_each_node_is_handled_independently(self):
        sandbox = self.run_with_nodes(
            show_node_line('aws-compute-0', 'DOWN+CLOUD+POWERED_DOWN'),
            show_node_line('aws-compute-1', 'IDLE+CLOUD+POWERED_DOWN'),
            show_node_line('aws-compute-2', 'IDLE*+CLOUD'),
            show_node_line('aws-compute-3', 'COMPLETING+DRAIN+CLOUD'),
        )
        changes = sandbox.state_changes()
        self.assertIn(('aws-compute-0', 'IDLE'), changes)
        self.assertIn(('aws-compute-2', 'POWER_DOWN'), changes)
        self.assertIn(('aws-compute-3', 'DOWN'), changes)
        self.assertEqual([c for c in changes if c[0] == 'aws-compute-1'], [])

    def test_empty_output_is_survived(self):
        sandbox = Sandbox(partitions=base_partitions(base_nodegroup(MaxNodes=4)))
        self.sandboxes.append(sandbox)
        sandbox.write_file('scontrol_show_node', '')
        sandbox.run('change_state.py', expect_returncode=0)
        self.assertEqual(sandbox.state_changes(), [])


class TestScontrolFailure(ChangeStateTestCase):
    """A failed transition must be logged as a failure.

    Before update_node() checked the exit code, change_state.py logged
    'Set node X to state Y' for updates that exited 1 — which is what hid the
    power-save bug in production.
    """

    def test_failed_update_is_logged_as_an_error(self):
        sandbox = Sandbox(partitions=base_partitions(base_nodegroup(MaxNodes=4)))
        self.sandboxes.append(sandbox)
        sandbox.write_file('scontrol_show_node',
                           show_node_line('aws-compute-0', 'DOWN+CLOUD+POWERED_DOWN') + '\n')
        sandbox.fail_scontrol_for('all')
        sandbox.run('change_state.py', expect_returncode=0)

        log = sandbox.log()
        self.assertIn('Failed to set node aws-compute-0', log)
        self.assertNotIn('Set node aws-compute-0 to state IDLE', log,
                         'must not report success for an update that exited non-zero')


if __name__ == '__main__':
    unittest.main()
