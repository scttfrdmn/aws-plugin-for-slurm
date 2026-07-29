"""suspend.py termination behavior.

A failure here costs money: an instance the plugin declines to terminate keeps
billing, and Slurm believes the node is gone.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Sandbox, base_nodegroup, base_partitions  # noqa: E402


def tagged_instance(instance_id, node_name=None, extra_tags=None):
    tags = []
    if node_name is not None:
        tags.append({'Key': 'Name', 'Value': node_name})
    tags += extra_tags or []
    return {'InstanceId': instance_id, 'Tags': tags}


def describe(*instances):
    return {'Reservations': [{'Instances': list(instances)}] if instances else []}


class SuspendTestCase(unittest.TestCase):

    def setUp(self):
        self.sandboxes = []

    def tearDown(self):
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self, **kwargs):
        sandbox = Sandbox(**kwargs)
        self.sandboxes.append(sandbox)
        return sandbox


class TestSuspend(SuspendTestCase):

    def test_terminates_matching_instances(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe(
                tagged_instance('i-aaa', 'aws-compute-0'),
                tagged_instance('i-bbb', 'aws-compute-1'))})
        sandbox.run('suspend.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sorted(sandbox.terminated_instances()), ['i-aaa', 'i-bbb'])

    def test_filters_by_node_name_and_live_states(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe(tagged_instance('i-aaa', 'aws-compute-0'))})
        sandbox.run('suspend.py', 'aws-compute-0', expect_returncode=0)

        filters = {f['Name']: f['Values']
                   for f in sandbox.journal_calls('describe_instances')[0]['Filters']}
        self.assertEqual(filters['tag:Name'], ['aws-compute-0'])
        # An already-terminated instance must not be re-terminated.
        self.assertNotIn('terminated', filters['instance-state-name'])
        self.assertIn('running', filters['instance-state-name'])

    def test_nothing_to_terminate_is_not_an_error(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe()})
        sandbox.run('suspend.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sandbox.terminated_instances(), [])

    def test_missing_hostlist_exits_nonzero(self):
        sandbox = self.sandbox(partitions=base_partitions(base_nodegroup()))
        sandbox.run('suspend.py', expect_returncode=1)
        self.assertIn('Missing hostlist argument', sandbox.log())

    def test_unknown_partition_is_skipped(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe(tagged_instance('i-aaa', 'other-group-0'))})
        sandbox.run('suspend.py', 'other-group-0', expect_returncode=0)

        self.assertEqual(sandbox.journal_calls('describe_instances'), [])
        self.assertEqual(sandbox.terminated_instances(), [])

    def test_each_nodegroup_is_described_separately(self):
        sandbox = self.sandbox(
            partitions=base_partitions(
                base_nodegroup(NodeGroupName='small'),
                base_nodegroup(NodeGroupName='large')),
            scenario={'describe_instances': describe(tagged_instance('i-aaa', 'aws-small-0'))})
        sandbox.run('suspend.py', 'aws-small-0,aws-large-0', expect_returncode=0)

        self.assertEqual(len(sandbox.journal_calls('describe_instances')), 2)


class TestSuspendFailureHandling(SuspendTestCase):
    """Regressions for two bugs of the same shape as the v3.2 resume.py fixes."""

    def test_describe_failure_does_not_crash_the_run(self):
        # Before the fix, the except branch logged and fell through to an unassigned
        # response_describe: NameError killed suspend.py, so every instance in every
        # remaining node group stayed up and kept billing.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'errors': {'describe_instances': 'RequestLimitExceeded'}})
        proc = sandbox.run('suspend.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertNotIn('NameError', proc.stderr)
        self.assertIn('Failed to describe instances to terminate', sandbox.log())

    def test_describe_failure_in_one_nodegroup_does_not_stop_the_next(self):
        sandbox = self.sandbox(
            partitions=base_partitions(
                base_nodegroup(NodeGroupName='small'),
                base_nodegroup(NodeGroupName='large')),
            scenario={'describe_instances': [
                {'error': 'RequestLimitExceeded'},
                describe(tagged_instance('i-bbb', 'aws-large-0'))]})
        sandbox.run('suspend.py', 'aws-small-0,aws-large-0', expect_returncode=0)

        self.assertEqual(sandbox.terminated_instances(), ['i-bbb'],
                         'the second node group must still be suspended')

    def test_instance_without_name_tag_is_not_logged_as_the_previous_node(self):
        # Same stale-variable shape as the resume.py IP bug: node_name persisted from
        # the previous iteration, so two instances were logged under one node name.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe(
                tagged_instance('i-aaa', 'aws-compute-0'),
                tagged_instance('i-bbb', node_name=None))})
        sandbox.run('suspend.py', 'aws-compute-[0-1]', expect_returncode=0)

        log = sandbox.log()
        self.assertIn('Terminated instance aws-compute-0 i-aaa', log)
        self.assertNotIn('Terminated instance aws-compute-0 i-bbb', log,
                         'i-bbb has no Name tag and must not inherit i-aaa\'s node name')
        # Terminating it is still correct: it matched the tag filter.
        self.assertEqual(sorted(sandbox.terminated_instances()), ['i-aaa', 'i-bbb'])

    def test_terminate_failure_is_logged_as_an_error(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe(tagged_instance('i-aaa', 'aws-compute-0')),
                      'errors': {'terminate_instances': 'UnauthorizedOperation'}})
        sandbox.run('suspend.py', 'aws-compute-0', expect_returncode=0)

        log = sandbox.log()
        self.assertIn('Failed to terminate instance', log)
        self.assertIn('incurring charges', log,
                      'the operator needs to know the instance may still be billing')

    def test_terminate_failure_does_not_stop_other_instances(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'describe_instances': describe(
                          tagged_instance('i-aaa', 'aws-compute-0'),
                          tagged_instance('i-bbb', 'aws-compute-1')),
                      'errors': {'terminate_instances': 'UnauthorizedOperation'}})
        sandbox.run('suspend.py', 'aws-compute-[0-1]', expect_returncode=0)

        # Both were attempted even though the first raised.
        self.assertEqual(len(sandbox.journal_calls('terminate_instances')), 2)


if __name__ == '__main__':
    unittest.main()
