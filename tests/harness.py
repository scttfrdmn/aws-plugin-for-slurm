"""Sandbox for running the plugin scripts without AWS or a Slurm controller.

See tests/README.md for the design. The short version: each Sandbox is a temporary
directory holding a copy of the plugin, a fake boto3 that shadows the real one, stub
scontrol/sinfo binaries, and the config.json/partitions.json the test wants.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLUGIN_FILES = (
    'common.py',
    'resume.py',
    'suspend.py',
    'change_state.py',
    'generate_conf.py',
    'health_check.py',
)

# A config.json that passes validate_config. Tests override individual keys.
BASE_CONFIG = {
    'LogLevel': 'DEBUG',
    'LogFileName': 'aws_plugin.log',
    'SlurmBinPath': 'bin/',
    'SlurmConf': {
        'PrivateData': 'CLOUD',
        'ResumeProgram': '/nfs/slurm/etc/aws/resume.py',
        'SuspendProgram': '/nfs/slurm/etc/aws/suspend.py',
        'ResumeRate': 100,
        'SuspendRate': 100,
        'ResumeTimeout': 600,
        'SuspendTime': 350,
        'TreeWidth': 60000,
    },
}


# A node group that passes validate_partitions. Tests override individual keys.
def base_nodegroup(**overrides):
    nodegroup = {
        'NodeGroupName': 'compute',
        'MaxNodes': 8,
        'Region': 'us-east-1',
        'SlurmSpecifications': {'CPUs': '2'},
        'PurchasingOption': 'on-demand',
        'LaunchTemplateSpecification': {'LaunchTemplateName': 'lt-test', 'Version': '$Latest'},
        'LaunchTemplateOverrides': [{'InstanceType': 'c5.large'}],
        'SubnetIds': ['subnet-aaa'],
    }
    nodegroup.update(overrides)
    return nodegroup


def base_partitions(*nodegroups, **partition_overrides):
    partition = {
        'PartitionName': 'aws',
        'NodeGroups': list(nodegroups) if nodegroups else [base_nodegroup()],
    }
    partition.update(partition_overrides)
    return {'Partitions': [partition]}


# Stub scontrol.
#
# 'show hostnames' expands a hostlist, because resume.py calls it before doing
# anything else. Only the forms the plugin generates are handled: a bare name, or
# prefix-[range] with comma-separated single ids and low-high spans.
#
# Every invocation is appended to scontrol_calls.log. If a node name appears in
# scontrol_fail_nodes, 'update' exits 1 for it, which is how registration failure
# is driven.
SCONTROL_STUB = r'''#!/usr/bin/env python3
import os
import re
import sys

here = os.path.dirname(os.path.abspath(__file__))
sandbox = os.path.dirname(here)

with open(os.path.join(sandbox, 'scontrol_calls.log'), 'a') as f:
    f.write(' '.join(sys.argv[1:]) + '\n')

args = sys.argv[1:]

if len(args) >= 2 and args[0] == 'show' and args[1] == 'hostnames':
    for hostlist in args[2].split(','):
        match = re.match(r'^(.*)\[(.*)\]$', hostlist)
        if not match:
            print(hostlist)
            continue
        prefix, spec = match.groups()
        for part in spec.split(','):
            if '-' in part:
                low, high = part.split('-')
                for i in range(int(low), int(high) + 1):
                    print('%s%d' % (prefix, i))
            else:
                print('%s%s' % (prefix, part))
    sys.exit(0)

if args and args[0] == 'update':
    fail_file = os.path.join(sandbox, 'scontrol_fail_nodes')
    if os.path.exists(fail_file):
        with open(fail_file) as f:
            failing = [line.strip() for line in f if line.strip()]
        target = ''
        for arg in args:
            if arg.lower().startswith('nodename='):
                target = arg.split('=', 1)[1]
        # 'all' fails every update, which is the total-failure case.
        if 'all' in failing or target in failing:
            sys.stderr.write('slurm_update_node: Invalid node name specified\n')
            sys.exit(1)
    sys.exit(0)

# 'show node' output is supplied by the test, one line per node.
if len(args) >= 2 and args[0] == 'show' and args[1] == 'node':
    node_file = os.path.join(sandbox, 'scontrol_show_node')
    if os.path.exists(node_file):
        with open(node_file) as f:
            sys.stdout.write(f.read())
    sys.exit(0)

sys.exit(0)
'''

SINFO_STUB = r'''#!/usr/bin/env python3
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sandbox = os.path.dirname(here)

with open(os.path.join(sandbox, 'sinfo_calls.log'), 'a') as f:
    f.write(' '.join(sys.argv[1:]) + '\n')

out = os.path.join(sandbox, 'sinfo_output')
if os.path.exists(out):
    with open(out) as f:
        sys.stdout.write(f.read())
sys.exit(0)
'''


class Sandbox:
    """A temporary directory holding a runnable copy of the plugin."""

    def __init__(self, config=None, partitions=None, scenario=None):
        self.path = tempfile.mkdtemp(prefix='slurm-plugin-test-')

        for name in PLUGIN_FILES:
            shutil.copy(os.path.join(REPO_ROOT, name), os.path.join(self.path, name))

        # The fake boto3 lands beside the plugin, so it shadows the installed boto3:
        # python3 <sandbox>/resume.py puts <sandbox> at sys.path[0].
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        shutil.copy(os.path.join(tests_dir, 'fake_boto3.py'),
                    os.path.join(self.path, 'boto3.py'))

        # sitecustomize is imported automatically at interpreter startup, which is how
        # the clock and network get patched before resume.py imports them.
        shutil.copy(os.path.join(tests_dir, 'fake_env.py'),
                    os.path.join(self.path, 'sitecustomize.py'))

        bin_dir = os.path.join(self.path, 'bin')
        os.mkdir(bin_dir)
        self._write_exec(os.path.join(bin_dir, 'scontrol'), SCONTROL_STUB)
        self._write_exec(os.path.join(bin_dir, 'sinfo'), SINFO_STUB)

        self.write_config(config if config is not None else BASE_CONFIG)
        if partitions is not None:
            self.write_partitions(partitions)
        else:
            self.write_partitions(base_partitions())
        if scenario is not None:
            self.write_scenario(scenario)

    def _write_exec(self, path, content):
        with open(path, 'w') as f:
            f.write(content)
        os.chmod(path, 0o755)

    def _write_json(self, name, data):
        with open(os.path.join(self.path, name), 'w') as f:
            json.dump(data, f, indent=2)

    def write_config(self, data):
        # SlurmBinPath must be absolute: resume.py runs with cwd=sandbox, but relying
        # on that would make the stub lookup silently cwd-dependent.
        data = json.loads(json.dumps(data))
        if data.get('SlurmBinPath') == 'bin/':
            data['SlurmBinPath'] = os.path.join(self.path, 'bin') + '/'
        if data.get('LogFileName') == 'aws_plugin.log':
            data['LogFileName'] = os.path.join(self.path, 'aws_plugin.log')
        self._write_json('config.json', data)

    def write_partitions(self, data):
        self._write_json('partitions.json', data)

    def write_scenario(self, data):
        self._write_json('fake_ec2_scenario.json', data)

    def write_file(self, name, content):
        with open(os.path.join(self.path, name), 'w') as f:
            f.write(content)

    def fail_scontrol_for(self, *node_names):
        """Make 'scontrol update' exit 1 for these nodes ('all' for every node)."""
        self.write_file('scontrol_fail_nodes', '\n'.join(node_names) + '\n')

    def run(self, script, *args, no_sleep=True, healthy_ips='all', expect_returncode=None):
        """Run a plugin script the way Slurm would, and return the CompletedProcess.

        healthy_ips controls the readiness checks: 'all' (default) passes every node,
        a list of IPs passes only those, and [] fails every node. Without this, a
        health check against a fake IP would sit in a real TCP timeout.
        """
        env = dict(os.environ)
        env['FAKE_EC2_JOURNAL'] = os.path.join(self.path, 'fake_ec2_journal.jsonl')
        env['FAKE_EC2_SCENARIO'] = os.path.join(self.path, 'fake_ec2_scenario.json')
        if no_sleep:
            env['FAKE_NO_SLEEP'] = '1'
        env['FAKE_HEALTHY_IPS'] = (
            healthy_ips if isinstance(healthy_ips, str) else ','.join(healthy_ips))
        # sitecustomize lives in the sandbox, so the sandbox must be on PYTHONPATH.
        # Overwrite rather than append: an inherited PYTHONPATH could otherwise put the
        # real boto3 ahead of the sandbox copy.
        env['PYTHONPATH'] = self.path

        proc = subprocess.run(
            [sys.executable, os.path.join(self.path, script)] + list(args),
            cwd=self.path, env=env, capture_output=True, text=True, timeout=120,
        )
        if expect_returncode is not None and proc.returncode != expect_returncode:
            raise AssertionError(
                'Expected %s to exit %d, got %d\nstdout:\n%s\nstderr:\n%s'
                % (script, expect_returncode, proc.returncode, proc.stdout, proc.stderr)
            )
        return proc

    # -- assertions read these -------------------------------------------------

    def journal(self):
        """Every fake EC2 call, in order, as a list of dicts."""
        path = os.path.join(self.path, 'fake_ec2_journal.jsonl')
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def journal_calls(self, name):
        """The kwargs of each call to a named fake EC2 method."""
        return [entry['kwargs'] for entry in self.journal() if entry['method'] == name]

    def terminated_instances(self):
        """Every instance id passed to terminate_instances, flattened."""
        ids = []
        for kwargs in self.journal_calls('terminate_instances'):
            ids.extend(kwargs.get('InstanceIds', []))
        return ids

    def tagged_instances(self):
        """Map of instance id -> Name tag value, for every create_tags call."""
        result = {}
        for kwargs in self.journal_calls('create_tags'):
            name = None
            for tag in kwargs.get('Tags', []):
                if tag.get('Key') == 'Name':
                    name = tag.get('Value')
            for resource in kwargs.get('Resources', []):
                result[resource] = name
        return result

    def scontrol_calls(self):
        path = os.path.join(self.path, 'scontrol_calls.log')
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [line.rstrip('\n') for line in f if line.strip()]

    def registered_nodes(self):
        """Map of node name -> nodeaddr, for successful and attempted updates alike.

        Reads the scontrol argv log, so it reflects what the plugin asked for. Pair it
        with fail_scontrol_for() to distinguish attempted from succeeded.
        """
        result = {}
        for call in self.scontrol_calls():
            args = call.split(' ')
            if not args or args[0] != 'update':
                continue
            node_name = None
            nodeaddr = None
            for arg in args:
                lowered = arg.lower()
                if lowered.startswith('nodename='):
                    node_name = arg.split('=', 1)[1]
                elif lowered.startswith('nodeaddr='):
                    nodeaddr = arg.split('=', 1)[1]
            if node_name is not None and nodeaddr is not None:
                result[node_name] = nodeaddr
        return result

    def state_changes(self):
        """List of (node_name, state) from 'scontrol update ... state=X' calls."""
        changes = []
        for call in self.scontrol_calls():
            args = call.split(' ')
            if not args or args[0] != 'update':
                continue
            node_name = None
            state = None
            for arg in args:
                lowered = arg.lower()
                if lowered.startswith('nodename='):
                    node_name = arg.split('=', 1)[1]
                elif lowered.startswith('state='):
                    state = arg.split('=', 1)[1]
            if node_name is not None and state is not None:
                changes.append((node_name, state))
        return changes

    def log(self):
        path = os.path.join(self.path, 'aws_plugin.log')
        if not os.path.exists(path):
            return ''
        with open(path) as f:
            return f.read()

    def read(self, name):
        with open(os.path.join(self.path, name)) as f:
            return f.read()

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)
