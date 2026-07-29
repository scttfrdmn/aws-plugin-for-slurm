"""Mutation tests: reintroduce each fixed bug and confirm a test catches it.

A regression test that passes against the broken code pins nothing. This suite proves
the pinning by putting each bug back — one minimal textual reversal of the fix, applied
to a copy of today's plugin so nothing else differs — and asserting that the named tests
fail.

Slower than the rest of the suite (each mutation runs its tests in a fresh sandbox), so
it is skipped unless RUN_MUTATION_TESTS=1. CI runs it on push; leave it out of the inner
loop.

Adding a fix? Add a mutation. If none can be written for it, the fix has no regression
test.
"""

import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)

import harness  # noqa: E402


# (label, file, original snippet, mutated snippet, tests that must fail)
MUTATIONS = [
    (
        'resume.py async path: drop the per-instance IP reset',
        'resume.py',
        """                    ip_address = None
                    hostname = None
                    for reservation in response_describe['Reservations']:""",
        """                    for reservation in response_describe['Reservations']:""",
        ['test_resume_launch.TestAsyncLaunchStaleIPRegression'
         '.test_missing_middle_instance_does_not_inherit_previous_ip',
         'test_resume_launch.TestAsyncLaunchStaleIPRegression'
         '.test_missing_first_instance_does_not_abort_the_run'],
    ),
    (
        'common.py update_node: ignore the scontrol exit code',
        'common.py',
        "    run_scommand('scontrol', arguments, check=True)",
        "    run_scommand('scontrol', arguments)",
        ['test_resume_launch.TestSyncLaunchRegistrationFailure'
         '.test_total_registration_failure_terminates_allocation',
         'test_resume_launch.TestSyncLaunchRegistrationFailure'
         '.test_single_registration_failure_terminates_allocation'],
    ),
    (
        'resume.py sync launch: no teardown on registration failure',
        'resume.py',
        '            if failed_registrations:',
        '            if False:',
        ['test_resume_launch.TestSyncLaunchRegistrationFailure'
         '.test_total_registration_failure_terminates_allocation',
         'test_resume_launch.TestSyncLaunchRegistrationFailure'
         '.test_single_registration_failure_terminates_allocation'],
    ),
    (
        'resume.py: skipped nodes not counted as failed',
        'resume.py',
        '        nb_failed_nodes = nb_nodes_to_resume - node_id_index + nb_skipped_nodes',
        '        nb_failed_nodes = nb_nodes_to_resume - node_id_index',
        ['test_resume_launch.TestAsyncLaunchStaleIPRegression'
         '.test_skipped_nodes_are_counted_as_failed'],
    ),
    (
        'resume.py sync launch: accept a partial fleet',
        'resume.py',
        '            if len(all_instance_ids) < nb_nodes_to_resume:',
        '            if False:',
        ['test_resume_launch.TestSyncLaunch.test_partial_fleet_terminates_everything'],
    ),
    (
        'resume.py: no fail-fast when an instance reaches a terminal state',
        'resume.py',
        """                    raise RuntimeError(
                        'Instance %s entered state "%s" while waiting for readiness'
                        %(instance_id, state)
                    )""",
        """                    pass""",
        ['test_resume_launch.TestSyncLaunch.test_instance_dying_mid_wait_fails_fast'],
    ),
    (
        'resume.py: RequirePlacementGroup not honored',
        'resume.py',
        "        if mpi_options.get('RequirePlacementGroup', False) "
        "and 'PlacementGroupName' not in nodegroup:",
        '        if False:',
        ['test_resume_launch.TestPlacementGroups'
         '.test_require_placement_group_without_one_skips_launch'],
    ),
    (
        'suspend.py: describe failure falls through to an unassigned response',
        'suspend.py',
        """            logger.error('Failed to describe instances to terminate for partition=%s '
                         'nodegroup=%s - %s' %(partition_name, nodegroup_name, e))
            continue""",
        """            logger.error('Failed to describe instances - %s' %e)""",
        ['test_suspend.TestSuspendFailureHandling'
         '.test_describe_failure_does_not_crash_the_run',
         'test_suspend.TestSuspendFailureHandling'
         '.test_describe_failure_in_one_nodegroup_does_not_stop_the_next'],
    ),
    (
        'suspend.py: node_name leaks across instances',
        'suspend.py',
        """                node_name = None
                for tag in instance.get('Tags', []):""",
        """                for tag in instance.get('Tags', []):""",
        ['test_suspend.TestSuspendFailureHandling'
         '.test_instance_without_name_tag_is_not_logged_as_the_previous_node'],
    ),
    (
        'generate_conf.py: assert false on a malformed Gres',
        'generate_conf.py',
        """                        raise AssertionError(
                            'Invalid GRES specification "%s" in node group %s: expected '
                            'name:count or name:type:count'
                            %(value, common.get_node_name(partition, nodegroup)))""",
        """                        assert false, "Invalid GRES field in %" % nodegroup""",
        ['test_generate_conf.TestMalformedGres'
         '.test_single_field_gres_raises_a_useful_error',
         'test_generate_conf.TestMalformedGres'
         '.test_error_names_the_offending_value_and_nodegroup'],
    ),
    (
        "change_state.py: bare 'POWER' membership test",
        'change_state.py',
        "    in_power_save = any('POWER' in state for state in node_states)",
        "    in_power_save = 'POWER' in node_states",
        ['test_change_state.TestPowerSaveStates.test_down_and_powered_down_becomes_idle',
         'test_change_state.TestPowerSaveStates.test_drain_in_power_save_is_undrained'],
    ),
]


def _build_mutant(filename, original, mutated):
    """Copy the plugin to a temp dir with one fix reversed. Returns the dir."""
    mutant_dir = tempfile.mkdtemp(prefix='slurm-plugin-mutant-')
    for name in harness.PLUGIN_FILES:
        shutil.copy(os.path.join(REPO_ROOT, name), os.path.join(mutant_dir, name))
    # test_validate reads examples/ through REPO_ROOT; keep the path resolvable.
    os.symlink(os.path.join(REPO_ROOT, 'examples'), os.path.join(mutant_dir, 'examples'))

    path = os.path.join(mutant_dir, filename)
    with open(path) as f:
        source = f.read()

    occurrences = source.count(original)
    if occurrences != 1:
        shutil.rmtree(mutant_dir, ignore_errors=True)
        raise AssertionError(
            'Mutation anchor appears %d times in %s (expected exactly 1). The code '
            'changed and this mutation needs updating.' % (occurrences, filename))

    with open(path, 'w') as f:
        f.write(source.replace(original, mutated))
    return mutant_dir


@unittest.skipUnless(os.environ.get('RUN_MUTATION_TESTS') == '1',
                     'set RUN_MUTATION_TESTS=1 to run mutation tests')
class TestMutationsAreDetected(unittest.TestCase):

    def test_every_mutation_is_caught(self):
        saved_root = harness.REPO_ROOT
        survivors = []
        try:
            for label, filename, original, mutated, test_names in MUTATIONS:
                with self.subTest(mutation=label):
                    mutant_dir = _build_mutant(filename, original, mutated)
                    try:
                        # Point the sandbox builder at the mutated plugin.
                        harness.REPO_ROOT = mutant_dir
                        suite = unittest.TestLoader().loadTestsFromNames(test_names)
                        with open(os.devnull, 'w') as devnull:
                            result = unittest.TextTestRunner(
                                stream=devnull, verbosity=0).run(suite)
                        caught = len(result.failures) + len(result.errors)
                        if caught == 0:
                            survivors.append(label)
                        self.assertGreater(
                            caught, 0,
                            'Mutation survived: %s\nNo test failed, so nothing pins '
                            'this bug.' % label)
                    finally:
                        harness.REPO_ROOT = saved_root
                        shutil.rmtree(mutant_dir, ignore_errors=True)
        finally:
            harness.REPO_ROOT = saved_root

        self.assertEqual(survivors, [], 'mutations that no test caught: %s' % survivors)


if __name__ == '__main__':
    unittest.main()
