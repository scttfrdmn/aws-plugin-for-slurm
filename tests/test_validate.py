"""validate_config, validate_partitions, and warn_on_conflicting_settings.

The backwards-compatibility claim in the README and upgrade guide — that a v2
partitions.json is still valid — is asserted here against the real v2 examples.
"""

import copy
import json
import logging
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from harness import BASE_CONFIG, base_nodegroup, base_partitions  # noqa: E402


class TestValidateConfig(unittest.TestCase):

    def test_accepts_base_config(self):
        common.validate_config(copy.deepcopy(BASE_CONFIG))

    def test_rejects_missing_top_level_keys(self):
        for key in ('LogLevel', 'LogFileName', 'SlurmBinPath', 'SlurmConf'):
            config = copy.deepcopy(BASE_CONFIG)
            del config[key]
            with self.assertRaises(AssertionError, msg='deleting %s should fail' % key):
                common.validate_config(config)

    def test_rejects_bad_log_level(self):
        config = copy.deepcopy(BASE_CONFIG)
        config['LogLevel'] = 'VERBOSE'
        with self.assertRaises(AssertionError):
            common.validate_config(config)

    def test_rejects_missing_slurmconf_keys(self):
        for key in ('PrivateData', 'ResumeProgram', 'SuspendProgram', 'ResumeRate',
                    'SuspendRate', 'ResumeTimeout', 'SuspendTime', 'TreeWidth'):
            config = copy.deepcopy(BASE_CONFIG)
            del config['SlurmConf'][key]
            with self.assertRaises(AssertionError, msg='deleting %s should fail' % key):
                common.validate_config(config)

    def test_rejects_slurmconf_that_is_not_a_dict(self):
        config = copy.deepcopy(BASE_CONFIG)
        config['SlurmConf'] = 'ResumeTimeout=600'
        with self.assertRaises(AssertionError):
            common.validate_config(config)


class TestValidatePartitions(unittest.TestCase):

    def test_accepts_base_partitions(self):
        common.validate_partitions(base_partitions())

    def test_rejects_missing_partitions_key(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions({})

    def test_rejects_partitions_that_is_not_a_list(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions({'Partitions': {'PartitionName': 'aws'}})

    def test_rejects_bad_partition_name(self):
        # generate_conf.py interpolates these into slurm.conf and parse_node_names
        # splits on '-', so punctuation has to be refused.
        for name in ('aws-cloud', 'aws_cloud', 'aws cloud', '', 'aws.cloud'):
            data = base_partitions()
            data['Partitions'][0]['PartitionName'] = name
            with self.assertRaises(AssertionError, msg='%r should be rejected' % name):
                common.validate_partitions(data)

    def test_rejects_bad_nodegroup_name(self):
        for name in ('c5n-large', 'c5n_large', ''):
            data = base_partitions(base_nodegroup(NodeGroupName=name))
            with self.assertRaises(AssertionError, msg='%r should be rejected' % name):
                common.validate_partitions(data)

    def test_rejects_missing_nodegroup_keys(self):
        required = ('NodeGroupName', 'MaxNodes', 'Region', 'SlurmSpecifications',
                    'PurchasingOption', 'LaunchTemplateSpecification',
                    'LaunchTemplateOverrides', 'SubnetIds')
        for key in required:
            nodegroup = base_nodegroup()
            del nodegroup[key]
            with self.assertRaises(AssertionError, msg='deleting %s should fail' % key):
                common.validate_partitions(base_partitions(nodegroup))

    def test_rejects_non_integer_max_nodes(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions(base_partitions(base_nodegroup(MaxNodes='8')))

    def test_rejects_bad_purchasing_option(self):
        for option in ('Spot', 'SPOT', 'reserved', 'ondemand'):
            with self.assertRaises(AssertionError, msg='%r should be rejected' % option):
                common.validate_partitions(base_partitions(
                    base_nodegroup(PurchasingOption=option)))

    def test_accepts_both_purchasing_options(self):
        for option in ('spot', 'on-demand'):
            common.validate_partitions(base_partitions(
                base_nodegroup(PurchasingOption=option)))

    def test_rejects_wrong_types_for_container_fields(self):
        cases = (
            ('SlurmSpecifications', ['CPUs=2']),
            ('LaunchTemplateSpecification', 'lt-test'),
            ('LaunchTemplateOverrides', {'InstanceType': 'c5.large'}),
            ('SubnetIds', 'subnet-aaa'),
        )
        for key, value in cases:
            with self.assertRaises(AssertionError, msg='%s=%r should fail' % (key, value)):
                common.validate_partitions(base_partitions(base_nodegroup(**{key: value})))

    def test_rejects_partition_options_that_is_not_a_dict(self):
        data = base_partitions()
        data['Partitions'][0]['PartitionOptions'] = 'OverSubscribe=NO'
        with self.assertRaises(AssertionError):
            common.validate_partitions(data)


class TestValidateMPIFields(unittest.TestCase):
    """The v3.1 opt-in fields. All are optional; absence must stay valid."""

    def test_v2_nodegroup_without_mpi_fields_is_valid(self):
        common.validate_partitions(base_partitions(base_nodegroup()))

    def test_accepts_full_mpi_config(self):
        common.validate_partitions(base_partitions(base_nodegroup(
            EnableMPISupport=True,
            PlacementGroupName='mpi-cluster',
            MPIOptions={
                'WaitForAllNodes': True,
                'TimeoutSeconds': 300,
                'HealthChecks': ['slurmd', 'network'],
                'RequirePlacementGroup': True,
            },
        )))

    def test_rejects_non_boolean_enable_mpi_support(self):
        # 'true' and 1 are the plausible hand-edits; both must be refused rather than
        # silently truthy.
        for value in ('true', 1, 'yes'):
            with self.assertRaises(AssertionError, msg='%r should be rejected' % value):
                common.validate_partitions(base_partitions(
                    base_nodegroup(EnableMPISupport=value)))

    def test_rejects_non_string_placement_group(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions(base_partitions(
                base_nodegroup(PlacementGroupName=['mpi-cluster'])))

    def test_rejects_non_dict_mpi_options(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions(base_partitions(
                base_nodegroup(MPIOptions=['TimeoutSeconds=300'])))

    def test_rejects_bad_timeout_seconds(self):
        for value in (0, -1, '300', 300.5):
            with self.assertRaises(AssertionError, msg='%r should be rejected' % value):
                common.validate_partitions(base_partitions(
                    base_nodegroup(MPIOptions={'TimeoutSeconds': value})))

    def test_accepts_bool_timeout_is_a_known_wart(self):
        # bool is a subclass of int, so isinstance(True, int) passes and True > 0.
        # Documenting the current behavior rather than asserting it is desirable.
        common.validate_partitions(base_partitions(
            base_nodegroup(MPIOptions={'TimeoutSeconds': True})))

    def test_rejects_unknown_health_check(self):
        # 'nfs' was removed in v3.1 because it always returned success. It must not
        # validate again by accident.
        for check in ('nfs', 'ssh', 'munge', 'NETWORK'):
            with self.assertRaises(AssertionError, msg='%r should be rejected' % check):
                common.validate_partitions(base_partitions(
                    base_nodegroup(MPIOptions={'HealthChecks': [check]})))

    def test_accepts_known_health_checks_and_empty_list(self):
        for checks in ([], ['slurmd'], ['network'], ['slurmd', 'network']):
            common.validate_partitions(base_partitions(
                base_nodegroup(MPIOptions={'HealthChecks': checks})))

    def test_rejects_non_list_health_checks(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions(base_partitions(
                base_nodegroup(MPIOptions={'HealthChecks': 'slurmd'})))

    def test_rejects_non_boolean_wait_for_all_nodes(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions(base_partitions(
                base_nodegroup(MPIOptions={'WaitForAllNodes': 'false'})))

    def test_rejects_non_boolean_require_placement_group(self):
        with self.assertRaises(AssertionError):
            common.validate_partitions(base_partitions(
                base_nodegroup(MPIOptions={'RequirePlacementGroup': 'true'})))


class TestShippedExamples(unittest.TestCase):
    """Every examples/*.json must load and validate.

    CI checks JSON syntax separately; this checks the stronger property that the
    partition examples pass the validator the plugin actually runs.
    """

    def _examples(self):
        examples_dir = os.path.join(REPO_ROOT, 'examples')
        for name in sorted(os.listdir(examples_dir)):
            if name.endswith('.json'):
                yield name, os.path.join(examples_dir, name)

    def test_examples_exist(self):
        # Guard against the glob silently matching nothing.
        self.assertGreater(len(list(self._examples())), 0)

    def test_every_example_is_valid_json(self):
        for name, path in self._examples():
            with self.subTest(example=name):
                with open(path) as f:
                    json.load(f)

    def test_partition_examples_validate(self):
        for name, path in self._examples():
            with open(path) as f:
                data = json.load(f)
            if 'Partitions' not in data:
                continue
            with self.subTest(example=name):
                common.validate_partitions(data)

    def test_config_examples_validate(self):
        for name, path in self._examples():
            with open(path) as f:
                data = json.load(f)
            if 'SlurmConf' not in data:
                continue
            with self.subTest(example=name):
                common.validate_config(data)

    def test_both_kinds_of_example_were_actually_covered(self):
        # The two tests above skip files that lack the key, so assert each kind exists
        # rather than passing vacuously.
        partitions_seen, configs_seen = 0, 0
        for _, path in self._examples():
            with open(path) as f:
                data = json.load(f)
            if 'Partitions' in data:
                partitions_seen += 1
            if 'SlurmConf' in data:
                configs_seen += 1
        self.assertGreater(partitions_seen, 0, 'no partitions example found')
        self.assertGreater(configs_seen, 0, 'no config example found')


class _CapturingLogger(logging.Logger):
    """Collects warnings so the conflict checks can be asserted on."""

    def __init__(self):
        super().__init__('test')
        self.warnings = []

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(msg % args if args else msg)


class TestWarnOnConflictingSettings(unittest.TestCase):
    """Startup warnings. These are advisory: the plugin must never refuse to launch."""

    def setUp(self):
        self._saved_logger = common.logger
        self.logger = _CapturingLogger()
        common.logger = self.logger

    def tearDown(self):
        common.logger = self._saved_logger

    def _warn(self, config, partitions):
        common.warn_on_conflicting_settings(config, partitions)
        return self.logger.warnings

    def _config(self, **slurm_conf_overrides):
        config = copy.deepcopy(BASE_CONFIG)
        config['SlurmConf'].update(slurm_conf_overrides)
        return config

    def test_silent_for_plain_v2_config(self):
        self.assertEqual(self._warn(self._config(), base_partitions()), [])

    def test_silent_when_timeout_has_headroom(self):
        # ResumeTimeout 600, TimeoutSeconds 300 -> 420 <= 600, fine.
        warnings = self._warn(
            self._config(ResumeTimeout=600),
            base_partitions(base_nodegroup(EnableMPISupport=True,
                                           MPIOptions={'TimeoutSeconds': 300})))
        self.assertEqual([w for w in warnings if 'headroom' in w], [])

    def test_warns_when_timeout_exceeds_resume_timeout(self):
        warnings = self._warn(
            self._config(ResumeTimeout=180),
            base_partitions(base_nodegroup(EnableMPISupport=True,
                                           MPIOptions={'TimeoutSeconds': 300})))
        self.assertTrue(any('headroom' in w for w in warnings), warnings)

    def test_warns_when_headroom_is_short_but_timeout_is_lower(self):
        # The rule is TimeoutSeconds + 120 <= ResumeTimeout, not just <.
        warnings = self._warn(
            self._config(ResumeTimeout=350),
            base_partitions(base_nodegroup(EnableMPISupport=True,
                                           MPIOptions={'TimeoutSeconds': 300})))
        self.assertTrue(any('headroom' in w for w in warnings), warnings)

    def test_default_timeout_is_used_when_unspecified(self):
        # No TimeoutSeconds means the 300s default, which needs 420s of ResumeTimeout.
        warnings = self._warn(
            self._config(ResumeTimeout=300),
            base_partitions(base_nodegroup(EnableMPISupport=True)))
        self.assertTrue(any('headroom' in w for w in warnings), warnings)

    def test_no_timeout_warning_when_sync_launch_is_disabled(self):
        # WaitForAllNodes=false means resume.py does not block, so ResumeTimeout
        # headroom is irrelevant.
        warnings = self._warn(
            self._config(ResumeTimeout=180),
            base_partitions(base_nodegroup(
                EnableMPISupport=True,
                MPIOptions={'TimeoutSeconds': 300, 'WaitForAllNodes': False})))
        self.assertEqual([w for w in warnings if 'headroom' in w], [])

    def test_no_warning_for_nodegroup_without_mpi(self):
        warnings = self._warn(
            self._config(ResumeTimeout=60),
            base_partitions(base_nodegroup(MPIOptions={'TimeoutSeconds': 300})))
        self.assertEqual(warnings, [])

    def test_warns_when_resume_rate_below_max_nodes(self):
        warnings = self._warn(
            self._config(ResumeRate=4, ResumeTimeout=600),
            base_partitions(base_nodegroup(MaxNodes=16, EnableMPISupport=True)))
        self.assertTrue(any('ResumeRate' in w for w in warnings), warnings)

    def test_silent_when_resume_rate_covers_max_nodes(self):
        warnings = self._warn(
            self._config(ResumeRate=100, ResumeTimeout=600),
            base_partitions(base_nodegroup(MaxNodes=16, EnableMPISupport=True)))
        self.assertEqual([w for w in warnings if 'ResumeRate' in w], [])

    # -- OverSubscribe ---------------------------------------------------------

    def _oversubscribe_warnings(self, value):
        data = base_partitions(base_nodegroup(EnableMPISupport=True))
        if value is not None:
            data['Partitions'][0]['PartitionOptions'] = {'OverSubscribe': value}
        warnings = self._warn(self._config(ResumeTimeout=600), data)
        return [w for w in warnings if 'OverSubscribe' in w]

    def test_warns_on_oversubscribe_values_that_permit_sharing(self):
        for value in ('YES', 'FORCE', 'yes', 'force', 'FORCE:2', 'YES:4'):
            with self.subTest(oversubscribe=value):
                self.assertTrue(self._oversubscribe_warnings(value),
                                'expected a warning for OverSubscribe=%s' % value)

    def test_silent_on_oversubscribe_values_that_give_whole_nodes(self):
        # EXCLUSIVE is stricter than NO, not looser: warning on it was a false
        # positive caught before release.
        for value in ('NO', 'no', 'EXCLUSIVE', 'exclusive'):
            with self.subTest(oversubscribe=value):
                self.assertEqual(self._oversubscribe_warnings(value), [],
                                 'OverSubscribe=%s should not warn' % value)

    def test_silent_when_oversubscribe_unset(self):
        self.assertEqual(self._oversubscribe_warnings(None), [])

    def test_no_oversubscribe_warning_without_mpi(self):
        data = base_partitions(base_nodegroup())
        data['Partitions'][0]['PartitionOptions'] = {'OverSubscribe': 'YES'}
        warnings = self._warn(self._config(), data)
        self.assertEqual([w for w in warnings if 'OverSubscribe' in w], [])

    def test_oversubscribe_checked_once_per_partition(self):
        # It is a partition-level setting; two MPI node groups must not double-warn.
        data = base_partitions(
            base_nodegroup(NodeGroupName='a', EnableMPISupport=True),
            base_nodegroup(NodeGroupName='b', EnableMPISupport=True),
        )
        data['Partitions'][0]['PartitionOptions'] = {'OverSubscribe': 'YES'}
        warnings = self._warn(self._config(ResumeTimeout=600), data)
        self.assertEqual(len([w for w in warnings if 'OverSubscribe' in w]), 1, warnings)

    def test_missing_slurm_conf_values_do_not_raise(self):
        # config.json need not carry ResumeTimeout/ResumeRate; the checks must skip
        # rather than crash on None.
        config = copy.deepcopy(BASE_CONFIG)
        del config['SlurmConf']['ResumeTimeout']
        del config['SlurmConf']['ResumeRate']
        self._warn(config, base_partitions(base_nodegroup(EnableMPISupport=True)))

    def test_empty_input_does_not_raise(self):
        self._warn({}, {})


if __name__ == '__main__':
    unittest.main()
