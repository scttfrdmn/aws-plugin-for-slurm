"""resume.py launch decisions, driven through a fake EC2 and a stub scontrol.

Each test asserts on what the plugin did — which instances it tagged, registered, or
terminated — rather than on log text, so a reworded message does not fail the suite.

Several tests are regressions for specific bugs; those name the bug.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scenarios  # noqa: E402
from harness import Sandbox, BASE_CONFIG, base_nodegroup, base_partitions  # noqa: E402


def mpi_nodegroup(**overrides):
    """A node group with synchronous launch enabled."""
    options = {'EnableMPISupport': True, 'NodeGroupName': 'compute'}
    options.update(overrides)
    return base_nodegroup(**options)


class SandboxTestCase(unittest.TestCase):

    def setUp(self):
        self.sandboxes = []

    def tearDown(self):
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self, **kwargs):
        sandbox = Sandbox(**kwargs)
        self.sandboxes.append(sandbox)
        return sandbox


class TestAsyncLaunch(SandboxTestCase):
    """The v2 path: register each node as its instance appears, no waiting."""

    def test_full_launch_registers_every_node(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.all_running(3)]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertEqual(sandbox.registered_nodes(), {
            'aws-compute-0': '10.0.0.10',
            'aws-compute-1': '10.0.0.11',
            'aws-compute-2': '10.0.0.12',
        })
        self.assertEqual(sandbox.terminated_instances(), [],
                         'async launch must never terminate on its own')

    def test_partial_fleet_registers_what_it_got(self):
        # Deliberate contrast with sync launch: v2 behavior keeps a short allocation.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-3]', expect_returncode=0)

        self.assertEqual(sorted(sandbox.registered_nodes()),
                         ['aws-compute-0', 'aws-compute-1'])
        self.assertEqual(sandbox.terminated_instances(), [])
        self.assertIn('Failed to launch 2 nodes', sandbox.log())

    def test_tags_carry_substituted_node_name(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sandbox.tagged_instances(), {
            scenarios.instance_id(0): 'aws-compute-0',
            scenarios.instance_id(1): 'aws-compute-1',
        })

    def test_custom_tags_get_context_substitution(self):
        nodegroup = base_nodegroup(Tags=[
            {'Key': 'NodeAddr', 'Value': '{ip_address}'},
            {'Key': 'Host', 'Value': '{hostname}'},
        ])
        sandbox = self.sandbox(
            partitions=base_partitions(nodegroup),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        tags = {tag['Key']: tag['Value'] for tag in sandbox.journal_calls('create_tags')[0]['Tags']}
        self.assertEqual(tags['NodeAddr'], '10.0.0.10')
        self.assertEqual(tags['Host'], 'ip-10-0-0-10')

    def test_nodes_split_across_reservations_map_in_order(self):
        # A fleet that filled from several overrides returns several Instances entries;
        # node ids are assigned across the flattened sequence.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(4, per_reservation=2),
                      'describe_instances': [scenarios.all_running(4)]})
        sandbox.run('resume.py', 'aws-compute-[0-3]', expect_returncode=0)

        self.assertEqual(sandbox.registered_nodes(), {
            'aws-compute-0': '10.0.0.10',
            'aws-compute-1': '10.0.0.11',
            'aws-compute-2': '10.0.0.12',
            'aws-compute-3': '10.0.0.13',
        })

    def test_unknown_partition_is_skipped(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'other-group-0', expect_returncode=0)

        self.assertEqual(sandbox.journal_calls('create_fleet'), [],
                         'must not launch anything for an unknown partition')
        self.assertIn('not in partition.json', sandbox.log())

    def test_create_fleet_failure_is_survived(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'errors': {'create_fleet': 'UnauthorizedOperation'}})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sandbox.registered_nodes(), {})
        self.assertIn('Failed to launch nodes', sandbox.log())

    def test_fleet_errors_are_logged_with_codes(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(
                          1, errors=[scenarios.fleet_error('InsufficientInstanceCapacity')]),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertIn('InsufficientInstanceCapacity', sandbox.log())


class TestAsyncLaunchStaleIPRegression(SandboxTestCase):
    """Regression: the async path reused the previous instance's IP.

    Before the fix, `ip_address` was only assigned inside the match loop, so an
    instance missing from describe_instances kept the prior iteration's value and a
    second Slurm node was registered against one instance. On the first iteration it
    raised NameError and aborted the whole resume run.
    """

    def test_missing_middle_instance_does_not_inherit_previous_ip(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            # Instance 1 absent from the response entirely.
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.describe(['running', None, 'running'])]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        registered = sandbox.registered_nodes()
        self.assertNotIn('aws-compute-1', registered,
                         'node 1 has no IP and must not be registered')
        self.assertEqual(registered.get('aws-compute-0'), '10.0.0.10')
        self.assertEqual(registered.get('aws-compute-2'), '10.0.0.12')
        # The bug's signature: two node names sharing one address.
        self.assertEqual(len(set(registered.values())), len(registered),
                         'no two nodes may share an IP: %s' % registered)

    def test_missing_first_instance_does_not_abort_the_run(self):
        # This was the NameError case. Nodes after it must still be registered.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.describe([None, 'running', 'running'])]})
        proc = sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertNotIn('NameError', proc.stderr)
        self.assertEqual(sandbox.registered_nodes(), {
            'aws-compute-1': '10.0.0.11',
            'aws-compute-2': '10.0.0.12',
        })

    def test_running_instance_without_private_ip_is_skipped(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [
                          scenarios.describe(['running', 'running'], ips=[None, '10.0.0.11'])]})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sandbox.registered_nodes(), {'aws-compute-1': '10.0.0.11'})

    def test_skipped_nodes_are_counted_as_failed(self):
        # nb_failed_nodes must include nodes whose instance launched but could not be
        # configured, not just the fleet shortfall.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.describe(['running', None, None])]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertIn('Failed to launch 2 nodes', sandbox.log())


class TestSyncLaunch(SandboxTestCase):
    """All-or-nothing launch: the allocation is complete or it is given back."""

    def test_full_allocation_registers_every_node(self):
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(4),
                      'describe_instances': [scenarios.all_running(4)]})
        sandbox.run('resume.py', 'aws-compute-[0-3]', expect_returncode=0)

        self.assertEqual(len(sandbox.registered_nodes()), 4)
        self.assertEqual(sandbox.terminated_instances(), [])
        self.assertIn('all 4 nodes configured and ready', sandbox.log())

    def test_partial_fleet_terminates_everything(self):
        # Requested 4, got 2. A tightly-coupled job cannot use 2, so give both back.
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-3]', expect_returncode=0)

        self.assertEqual(sorted(sandbox.terminated_instances()),
                         [scenarios.instance_id(0), scenarios.instance_id(1)])
        self.assertEqual(sandbox.registered_nodes(), {},
                         'no node may be registered from a partial allocation')

    def test_instance_dying_mid_wait_fails_fast(self):
        # Fail as soon as an instance reaches a terminal state rather than burning the
        # whole timeout on a node that will never appear.
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(MPIOptions={'TimeoutSeconds': 600})),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [
                          scenarios.describe(['running', 'pending', 'pending']),
                          scenarios.describe(['running', 'terminated', 'pending'])]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertEqual(len(sandbox.terminated_instances()), 3)
        self.assertEqual(sandbox.registered_nodes(), {})
        self.assertIn('entered state "terminated"', sandbox.log())

    def test_timeout_terminates_the_whole_allocation(self):
        # Instance 2 never becomes running. The fake clock makes this instant.
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(MPIOptions={'TimeoutSeconds': 30})),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [
                          scenarios.describe(['running', 'running', 'pending'])]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertEqual(len(sandbox.terminated_instances()), 3)
        self.assertEqual(sandbox.registered_nodes(), {})
        self.assertIn('Sync launch failed', sandbox.log())

    def test_instances_becoming_ready_over_several_polls_succeeds(self):
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(MPIOptions={'TimeoutSeconds': 600})),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [
                          scenarios.describe(['pending', 'pending', 'pending']),
                          scenarios.describe(['running', 'pending', 'pending']),
                          scenarios.describe(['running', 'running', 'running'])]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertEqual(len(sandbox.registered_nodes()), 3)
        self.assertEqual(sandbox.terminated_instances(), [])

    def test_single_node_uses_async_path(self):
        # Sync launch is pointless for one node; there is no partial allocation.
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        self.assertEqual(sandbox.registered_nodes(), {'aws-compute-0': '10.0.0.10'})
        self.assertIn('Sync launch not needed for a single node', sandbox.log())

    def test_wait_for_all_nodes_false_uses_async_path(self):
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(
                MPIOptions={'WaitForAllNodes': False})),
            # A short fleet would be terminated under sync launch; here it is kept.
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-3]', expect_returncode=0)

        self.assertEqual(len(sandbox.registered_nodes()), 2)
        self.assertEqual(sandbox.terminated_instances(), [])
        self.assertIn('Sync launch disabled', sandbox.log())

    def test_health_checks_can_be_disabled(self):
        # An empty HealthChecks list means readiness is 'running' alone. The default
        # slurmd port check would fail against a fake IP, so this also proves the
        # setting is read.
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(
                MPIOptions={'HealthChecks': [], 'TimeoutSeconds': 30})),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(len(sandbox.registered_nodes()), 2)


class TestSyncLaunchRegistrationFailure(SandboxTestCase):
    """Regression: sync launch reported success when no node was registered.

    update_node() ignored scontrol's exit code, so 'all N nodes configured and ready'
    was logged even when every registration failed — the exact false success that
    all-or-nothing launch exists to prevent.
    """

    def test_total_registration_failure_terminates_allocation(self):
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(MPIOptions={'HealthChecks': []})),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.all_running(3)]})
        sandbox.fail_scontrol_for('all')
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertEqual(len(sandbox.terminated_instances()), 3)
        self.assertNotIn('nodes configured and ready', sandbox.log(),
                         'must not claim success when every registration failed')

    def test_single_registration_failure_terminates_allocation(self):
        # One unreachable node makes the allocation incomplete, same as a short fleet.
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(MPIOptions={'HealthChecks': []})),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.all_running(3)]})
        sandbox.fail_scontrol_for('aws-compute-1')
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        self.assertEqual(len(sandbox.terminated_instances()), 3)
        self.assertIn('could not be registered in Slurm', sandbox.log())

    def test_async_path_registration_failure_does_not_terminate(self):
        # v2 semantics are unchanged: log the failure, keep the rest.
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.fail_scontrol_for('aws-compute-0')
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sandbox.terminated_instances(), [])
        self.assertIn('Failed to update node information in Slurm', sandbox.log())


class TestPlacementGroups(SandboxTestCase):

    def test_placement_group_is_applied_to_every_override(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup(PlacementGroupName='mpi-cluster')),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        overrides = sandbox.journal_calls('create_fleet')[0][
            'LaunchTemplateConfigs'][0]['Overrides']
        self.assertTrue(overrides)
        for override in overrides:
            self.assertEqual(override['Placement'], {'GroupName': 'mpi-cluster'})

    def test_require_placement_group_without_one_skips_launch(self):
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(
                MPIOptions={'RequirePlacementGroup': True})),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(sandbox.journal_calls('create_fleet'), [],
                         'must not launch when a required placement group is missing')

    def test_require_placement_group_with_one_launches(self):
        sandbox = self.sandbox(
            partitions=base_partitions(mpi_nodegroup(
                PlacementGroupName='mpi-cluster',
                MPIOptions={'RequirePlacementGroup': True, 'HealthChecks': []})),
            scenario={'create_fleet': scenarios.fleet(2),
                      'describe_instances': [scenarios.all_running(2)]})
        sandbox.run('resume.py', 'aws-compute-[0-1]', expect_returncode=0)

        self.assertEqual(len(sandbox.journal_calls('create_fleet')), 1)
        self.assertEqual(len(sandbox.registered_nodes()), 2)

    def test_placement_group_with_multiple_subnets_warns(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup(
                PlacementGroupName='mpi-cluster',
                SubnetIds=['subnet-aaa', 'subnet-bbb'])),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        self.assertIn('cannot span availability zones', sandbox.log())


class TestFleetRequest(SandboxTestCase):
    """The CreateFleet request the plugin builds."""

    def test_target_capacity_matches_requested_nodes(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(3),
                      'describe_instances': [scenarios.all_running(3)]})
        sandbox.run('resume.py', 'aws-compute-[0-2]', expect_returncode=0)

        request = sandbox.journal_calls('create_fleet')[0]
        self.assertEqual(request['TargetCapacitySpecification']['TotalTargetCapacity'], 3)
        self.assertEqual(request['TargetCapacitySpecification']['DefaultTargetCapacityType'],
                         'on-demand')
        self.assertEqual(request['Type'], 'instant')

    def test_overrides_are_crossed_with_subnets(self):
        # Two instance types times three subnets is six overrides.
        nodegroup = base_nodegroup(
            LaunchTemplateOverrides=[{'InstanceType': 'c5.large'},
                                     {'InstanceType': 'c5.xlarge'}],
            SubnetIds=['subnet-aaa', 'subnet-bbb', 'subnet-ccc'])
        sandbox = self.sandbox(
            partitions=base_partitions(nodegroup),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        overrides = sandbox.journal_calls('create_fleet')[0][
            'LaunchTemplateConfigs'][0]['Overrides']
        self.assertEqual(len(overrides), 6)
        self.assertEqual(
            sorted({(o['InstanceType'], o['SubnetId']) for o in overrides}),
            sorted([(t, s) for t in ('c5.large', 'c5.xlarge')
                    for s in ('subnet-aaa', 'subnet-bbb', 'subnet-ccc')]))
        for override in overrides:
            self.assertEqual(override['WeightedCapacity'], 1)

    def test_spot_options_force_terminate_interruption_behavior(self):
        nodegroup = base_nodegroup(PurchasingOption='spot',
                                   SpotOptions={'AllocationStrategy': 'capacity-optimized'})
        sandbox = self.sandbox(
            partitions=base_partitions(nodegroup),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        request = sandbox.journal_calls('create_fleet')[0]
        self.assertEqual(request['SpotOptions']['InstanceInterruptionBehavior'], 'terminate')
        self.assertEqual(request['SpotOptions']['AllocationStrategy'], 'capacity-optimized')

    def test_on_demand_options_are_passed_through(self):
        nodegroup = base_nodegroup(OnDemandOptions={'AllocationStrategy': 'lowest-price'})
        sandbox = self.sandbox(
            partitions=base_partitions(nodegroup),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        request = sandbox.journal_calls('create_fleet')[0]
        self.assertEqual(request['OnDemandOptions'], {'AllocationStrategy': 'lowest-price'})

    def test_launch_template_specification_is_passed_through(self):
        sandbox = self.sandbox(
            partitions=base_partitions(base_nodegroup()),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=0)

        config = sandbox.journal_calls('create_fleet')[0]['LaunchTemplateConfigs'][0]
        self.assertEqual(config['LaunchTemplateSpecification'],
                         {'LaunchTemplateName': 'lt-test', 'Version': '$Latest'})


class TestMultipleNodeGroups(SandboxTestCase):

    def test_each_nodegroup_gets_its_own_fleet(self):
        sandbox = self.sandbox(
            partitions=base_partitions(
                base_nodegroup(NodeGroupName='small',
                               LaunchTemplateOverrides=[{'InstanceType': 'c5.large'}]),
                base_nodegroup(NodeGroupName='large',
                               LaunchTemplateOverrides=[{'InstanceType': 'c5.9xlarge'}])),
            scenario={'create_fleet': scenarios.fleet(1),
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-small-0,aws-large-0', expect_returncode=0)

        requests = sandbox.journal_calls('create_fleet')
        self.assertEqual(len(requests), 2)
        types = {r['LaunchTemplateConfigs'][0]['Overrides'][0]['InstanceType']
                 for r in requests}
        self.assertEqual(types, {'c5.large', 'c5.9xlarge'})

    def test_one_nodegroup_failing_does_not_stop_the_other(self):
        sandbox = self.sandbox(
            partitions=base_partitions(
                base_nodegroup(NodeGroupName='small'),
                base_nodegroup(NodeGroupName='large')),
            # First create_fleet returns nothing, second returns an instance.
            scenario={'create_fleet': [scenarios.fleet(0), scenarios.fleet(1)],
                      'describe_instances': [scenarios.all_running(1)]})
        sandbox.run('resume.py', 'aws-small-0,aws-large-0', expect_returncode=0)

        self.assertEqual(len(sandbox.journal_calls('create_fleet')), 2)
        self.assertEqual(len(sandbox.registered_nodes()), 1)


class TestResumeArguments(SandboxTestCase):

    def test_missing_hostlist_exits_nonzero(self):
        sandbox = self.sandbox(partitions=base_partitions(base_nodegroup()))
        sandbox.run('resume.py', expect_returncode=1)
        self.assertIn('Missing hostlist argument', sandbox.log())

    def test_invalid_config_exits_nonzero(self):
        config = dict(BASE_CONFIG)
        config = {k: v for k, v in config.items() if k != 'SlurmConf'}
        sandbox = self.sandbox(config=config, partitions=base_partitions(base_nodegroup()))
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=1)
        self.assertIn('config.json is invalid', sandbox.log())

    def test_invalid_partitions_exits_nonzero(self):
        sandbox = self.sandbox(partitions={'Partitions': [{'PartitionName': 'aws'}]})
        sandbox.run('resume.py', 'aws-compute-0', expect_returncode=1)
        self.assertIn('is invalid', sandbox.log())


if __name__ == '__main__':
    unittest.main()
