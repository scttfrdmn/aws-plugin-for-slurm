"""A fake boto3 that shadows the real one inside a test sandbox.

Copied into the sandbox as boto3.py, so `import boto3` from the plugin finds this
instead of the installed package. Records every call to a JSON-lines journal and
answers from a scenario file; makes no network calls.

Scenario format (all keys optional):

    {
      "create_fleet": {...}            single response, or
      "create_fleet": [{...}, {...}]   one per successive call
      "describe_instances": [{...}]    one per successive call; last repeats
      "create_tags": {"error": "..."}  raise instead of returning
      "terminate_instances": {...}
      "errors": {"create_fleet": "AccessDenied"}   raise on this method
    }

`describe_instances` taking a list is what lets a test walk an instance from pending
to running across polls, which is how the sync-launch wait is exercised.

The clock and network patches live in fake_env.py, not here: this module models the
AWS API and nothing else.
"""

import json
import os


_JOURNAL = os.environ.get('FAKE_EC2_JOURNAL')
_SCENARIO_PATH = os.environ.get('FAKE_EC2_SCENARIO')


def _load_scenario():
    if not _SCENARIO_PATH or not os.path.exists(_SCENARIO_PATH):
        return {}
    with open(_SCENARIO_PATH) as f:
        return json.load(f)


class FakeClientError(Exception):
    """Stands in for botocore.exceptions.ClientError.

    The plugin only ever catches bare Exception, so the type does not matter to it.
    """


# Call counts are process-wide, not per-client: the plugin builds a fresh client for
# every node group (common.get_ec2_client), so per-client counters would restart and
# every node group would see the first scenario entry.
_call_counts = {}


class FakeEC2Client:

    def __init__(self, scenario):
        self._scenario = scenario

    def _record(self, method, kwargs):
        if not _JOURNAL:
            return
        with open(_JOURNAL, 'a') as f:
            f.write(json.dumps({'method': method, 'kwargs': kwargs}, default=str) + '\n')

    def _next_response(self, method):
        """Return this call's response, walking a list one entry per call."""
        index = _call_counts.get(method, 0)
        _call_counts[method] = index + 1

        configured = self._scenario.get(method)
        if configured is None:
            return {}
        if isinstance(configured, list):
            if not configured:
                return {}
            # Past the end, the last entry repeats: a steady state after the
            # transitions the test cares about.
            return configured[min(index, len(configured) - 1)]
        return configured

    def _dispatch(self, method, kwargs):
        self._record(method, kwargs)

        errors = self._scenario.get('errors', {})
        if method in errors:
            raise FakeClientError('%s: %s' % (method, errors[method]))

        response = self._next_response(method)
        if isinstance(response, dict) and 'error' in response:
            raise FakeClientError('%s: %s' % (method, response['error']))
        return response

    def create_fleet(self, **kwargs):
        return self._dispatch('create_fleet', kwargs)

    def describe_instances(self, **kwargs):
        return self._dispatch('describe_instances', kwargs)

    def create_tags(self, **kwargs):
        return self._dispatch('create_tags', kwargs)

    def terminate_instances(self, **kwargs):
        return self._dispatch('terminate_instances', kwargs)


class _FakeSession:

    def __init__(self, region_name=None, profile_name=None):
        self.region_name = region_name
        self.profile_name = profile_name

    def client(self, service_name, **kwargs):
        return client(service_name, **kwargs)


class _SessionModule:
    Session = _FakeSession


session = _SessionModule()


def client(service_name, **kwargs):
    if service_name != 'ec2':
        raise FakeClientError('fake boto3 only implements ec2, got %s' % service_name)
    return FakeEC2Client(_load_scenario())
