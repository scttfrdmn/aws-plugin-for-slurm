"""generate_conf.py output.

This writes the slurm.conf fragment an operator appends to their real slurm.conf, so a
malformed line is a cluster that will not start.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Sandbox, BASE_CONFIG, base_nodegroup, base_partitions  # noqa: E402


class GenerateConfTestCase(unittest.TestCase):

    def setUp(self):
        self.sandboxes = []

    def tearDown(self):
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def generate(self, partitions, config=None, expect_returncode=0):
        sandbox = Sandbox(config=config, partitions=partitions)
        self.sandboxes.append(sandbox)
        proc = sandbox.run('generate_conf.py', expect_returncode=expect_returncode)
        return sandbox, proc


class TestSlurmConf(GenerateConfTestCase):

    def test_slurm_conf_parameters_are_written(self):
        sandbox, _ = self.generate(base_partitions(base_nodegroup()))
        output = sandbox.read('slurm.conf.aws')

        self.assertIn('ResumeTimeout=600', output)
        self.assertIn('SuspendTime=350', output)
        self.assertIn('TreeWidth=60000', output)
        self.assertIn('PrivateData=CLOUD', output)

    def test_node_line_declares_cloud_state(self):
        # Cloud nodes must be State=CLOUD or Slurm will not call ResumeProgram.
        sandbox, _ = self.generate(base_partitions(base_nodegroup(MaxNodes=4)))
        output = sandbox.read('slurm.conf.aws')

        self.assertIn('NodeName=aws-compute-[0-3] State=CLOUD', output)
        self.assertIn('CPUs=2', output)

    def test_single_node_group_has_no_range_brackets(self):
        sandbox, _ = self.generate(base_partitions(base_nodegroup(MaxNodes=1)))
        self.assertIn('NodeName=aws-compute-0 State=CLOUD',
                      sandbox.read('slurm.conf.aws'))

    def test_partition_line_lists_every_nodegroup(self):
        sandbox, _ = self.generate(base_partitions(
            base_nodegroup(NodeGroupName='small', MaxNodes=2),
            base_nodegroup(NodeGroupName='large', MaxNodes=4)))
        output = sandbox.read('slurm.conf.aws')

        self.assertIn('PartitionName=aws', output)
        self.assertIn('Nodes=aws-small-[0-1],aws-large-[0-3]', output)
        self.assertIn('MaxTime=INFINITE State=UP', output)

    def test_partition_options_are_written(self):
        partitions = base_partitions(base_nodegroup())
        partitions['Partitions'][0]['PartitionOptions'] = {
            'OverSubscribe': 'EXCLUSIVE', 'Default': 'YES'}
        sandbox, _ = self.generate(partitions)
        output = sandbox.read('slurm.conf.aws')

        self.assertIn('OverSubscribe=EXCLUSIVE', output)
        self.assertIn('Default=YES', output)

    def test_multiple_partitions_are_written(self):
        partitions = {'Partitions': [
            base_partitions(base_nodegroup())['Partitions'][0],
            {'PartitionName': 'mpi', 'NodeGroups': [base_nodegroup(NodeGroupName='c5n')]},
        ]}
        sandbox, _ = self.generate(partitions)
        output = sandbox.read('slurm.conf.aws')

        self.assertIn('PartitionName=aws', output)
        self.assertIn('PartitionName=mpi', output)
        self.assertIn('NodeName=mpi-c5n-[0-7]', output)


class TestGresConf(GenerateConfTestCase):

    def _gres_nodegroup(self, gres, **overrides):
        specs = {'CPUs': '8', 'Gres': gres}
        return base_nodegroup(SlurmSpecifications=specs, **overrides)

    def test_gpu_with_name_and_count(self):
        sandbox, _ = self.generate(base_partitions(
            self._gres_nodegroup('gpu:4', MaxNodes=2)))
        output = sandbox.read('gres.conf.aws')

        self.assertIn('NodeName=aws-compute-[0-1]', output)
        self.assertIn('Name=gpu', output)
        self.assertIn('File=/dev/nvidia[0-3]', output)

    def test_gpu_with_name_type_and_count(self):
        sandbox, _ = self.generate(base_partitions(
            self._gres_nodegroup('gpu:a100:8', MaxNodes=1)))
        output = sandbox.read('gres.conf.aws')

        self.assertIn('Type=a100', output)
        self.assertIn('File=/dev/nvidia[0-7]', output)

    def test_single_gpu_uses_index_zero(self):
        # Must be /dev/nvidia[0], not /dev/nvidia[0--1].
        sandbox, _ = self.generate(base_partitions(self._gres_nodegroup('gpu:1')))
        output = sandbox.read('gres.conf.aws')

        self.assertIn('File=/dev/nvidia[0]', output)
        self.assertNotIn('nvidia[0--1]', output)

    def test_non_gpu_gres_gets_no_device_file(self):
        sandbox, _ = self.generate(base_partitions(self._gres_nodegroup('bandwidth:100')))
        output = sandbox.read('gres.conf.aws')

        self.assertIn('Name=bandwidth', output)
        self.assertNotIn('/dev/nvidia', output)

    def test_nodegroup_without_gres_writes_no_line(self):
        sandbox, _ = self.generate(base_partitions(base_nodegroup()))
        self.assertEqual(sandbox.read('gres.conf.aws').strip(), '')

    def test_gres_key_is_matched_case_insensitively(self):
        sandbox, _ = self.generate(base_partitions(
            base_nodegroup(SlurmSpecifications={'CPUs': '8', 'GRES': 'gpu:2'})))
        self.assertIn('Name=gpu', sandbox.read('gres.conf.aws'))


class TestMalformedGres(GenerateConfTestCase):
    """Regression: a malformed Gres raised NameError, not a useful message.

    The old code was `assert false, "Invalid GRES field in %" % nodegroup`, which had
    two bugs: `false` is undefined in Python, and the format string was incomplete. An
    operator with a typo got `NameError: name 'false' is not defined`.
    """

    def _run_malformed(self, gres):
        sandbox, proc = self.generate(
            base_partitions(base_nodegroup(SlurmSpecifications={'Gres': gres})),
            expect_returncode=1)
        return sandbox, proc

    def test_single_field_gres_raises_a_useful_error(self):
        sandbox, proc = self._run_malformed('gpu')

        self.assertNotIn('NameError', proc.stderr)
        self.assertNotIn("name 'false' is not defined", proc.stderr)
        self.assertIn('Invalid GRES specification', proc.stderr)

    def test_error_names_the_offending_value_and_nodegroup(self):
        sandbox, proc = self._run_malformed('gpu')

        self.assertIn('"gpu"', proc.stderr)
        self.assertIn('aws-compute', proc.stderr,
                      'the operator needs to know which node group to fix')

    def test_four_field_gres_raises(self):
        sandbox, proc = self._run_malformed('gpu:a100:sxm:8')
        self.assertIn('Invalid GRES specification', proc.stderr)

    def test_empty_gres_raises(self):
        sandbox, proc = self._run_malformed('')
        self.assertIn('Invalid GRES specification', proc.stderr)


class TestGeneratedConfIsWellFormed(GenerateConfTestCase):
    """Every emitted line must be a Slurm key=value line."""

    def test_no_line_has_a_dangling_key(self):
        partitions = base_partitions(
            base_nodegroup(NodeGroupName='cpu', MaxNodes=4),
            base_nodegroup(NodeGroupName='gpu', MaxNodes=2,
                           SlurmSpecifications={'CPUs': '8', 'Gres': 'gpu:a100:4'}))
        partitions['Partitions'][0]['PartitionOptions'] = {'OverSubscribe': 'NO'}
        sandbox, _ = self.generate(partitions)

        for filename in ('slurm.conf.aws', 'gres.conf.aws'):
            for lineno, line in enumerate(sandbox.read(filename).splitlines(), 1):
                if not line.strip():
                    continue
                with self.subTest(file=filename, line=lineno):
                    # Tokens are space-separated key=value pairs; Type= and File= are
                    # omitted entirely when empty, which leaves double spaces but no
                    # bare keys.
                    for token in line.split():
                        self.assertIn('=', token,
                                      'bare token %r in %s: %r' % (token, filename, line))
                        key = token.split('=', 1)[0]
                        self.assertTrue(key, 'empty key in %r' % line)

    def test_no_unsubstituted_placeholders(self):
        sandbox, _ = self.generate(base_partitions(base_nodegroup()))
        for filename in ('slurm.conf.aws', 'gres.conf.aws'):
            content = sandbox.read(filename)
            self.assertNotIn('%s', content)
            self.assertNotIn('{', content)


if __name__ == '__main__':
    unittest.main()
