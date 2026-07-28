"""get_node_name / get_node_range / parse_node_names.

These are pure functions and the only part of the plugin that can be imported
directly — common.py runs nothing at import time.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common  # noqa: E402


class TestGetNodeName(unittest.TestCase):

    def test_strings(self):
        self.assertEqual(common.get_node_name('aws', 'compute', 0), 'aws-compute-0')
        self.assertEqual(common.get_node_name('aws', 'compute', 42), 'aws-compute-42')

    def test_dicts(self):
        partition = {'PartitionName': 'mpi'}
        nodegroup = {'NodeGroupName': 'c5n'}
        self.assertEqual(common.get_node_name(partition, nodegroup, 3), 'mpi-c5n-3')

    def test_mixed_string_and_dict(self):
        self.assertEqual(common.get_node_name('mpi', {'NodeGroupName': 'c5n'}, 1), 'mpi-c5n-1')

    def test_omitted_id_gives_group_prefix(self):
        self.assertEqual(common.get_node_name('aws', 'compute'), 'aws-compute')

    def test_id_zero_is_not_treated_as_omitted(self):
        # get_node_name tests `node_id == ''`, and 0 == '' is False in Python 3, so
        # node 0 keeps its suffix. Worth pinning: `if not node_id` would break it.
        self.assertEqual(common.get_node_name('aws', 'compute', 0), 'aws-compute-0')


class TestGetNodeRange(unittest.TestCase):

    def test_multiple_nodes(self):
        self.assertEqual(
            common.get_node_range('aws', {'NodeGroupName': 'compute'}, 8),
            'aws-compute-[0-7]')

    def test_single_node_has_no_bracket(self):
        # A one-node group must not emit 'aws-compute-[0-0]'.
        self.assertEqual(
            common.get_node_range('aws', {'NodeGroupName': 'compute'}, 1),
            'aws-compute-0')

    def test_defaults_to_max_nodes(self):
        nodegroup = {'NodeGroupName': 'compute', 'MaxNodes': 4}
        self.assertEqual(common.get_node_range('aws', nodegroup), 'aws-compute-[0-3]')


class TestParseNodeNames(unittest.TestCase):

    def test_groups_by_partition_and_nodegroup(self):
        result = common.parse_node_names([
            'aws-compute-0', 'aws-compute-1', 'aws-gpu-0', 'mpi-c5n-7',
        ])
        self.assertEqual(result, {
            'aws': {'compute': ['0', '1'], 'gpu': ['0']},
            'mpi': {'c5n': ['7']},
        })

    def test_ignores_names_that_do_not_match(self):
        # Static on-prem nodes share the hostlist namespace and must be skipped, not
        # crash the run.
        result = common.parse_node_names([
            'aws-compute-0', 'onprem01', 'node-with-no-id', 'aws_compute_2',
        ])
        self.assertEqual(result, {'aws': {'compute': ['0']}})

    def test_empty_input(self):
        self.assertEqual(common.parse_node_names([]), {})

    def test_round_trip_with_get_node_name(self):
        names = [common.get_node_name('aws', 'compute', i) for i in range(3)]
        self.assertEqual(common.parse_node_names(names), {'aws': {'compute': ['0', '1', '2']}})

    def test_ids_are_strings_in_fleet_order(self):
        # resume.py indexes node_ids positionally against fleet instance ids, so order
        # is load-bearing.
        result = common.parse_node_names(['aws-compute-5', 'aws-compute-2', 'aws-compute-9'])
        self.assertEqual(result['aws']['compute'], ['5', '2', '9'])


if __name__ == '__main__':
    unittest.main()
