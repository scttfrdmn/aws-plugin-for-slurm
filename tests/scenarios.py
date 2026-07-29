"""Builders for fake EC2 responses.

Keeps the tests readable: `fleet(3)` instead of four levels of nested dicts. Shapes
follow the fields the plugin actually reads from CreateFleet and DescribeInstances.
"""


def instance_id(index):
    return 'i-%012x' % (index + 1)


def private_ip(index):
    return '10.0.0.%d' % (index + 10)


def hostname(index):
    return 'ip-%s' % private_ip(index).replace('.', '-')


def fleet(nb_instances, per_reservation=None, errors=None, fleet_id='fleet-test'):
    """A CreateFleet 'instant' response holding nb_instances ids.

    per_reservation groups the ids across several Instances entries, mirroring a fleet
    that filled from more than one launch template override.
    """
    ids = [instance_id(i) for i in range(nb_instances)]

    if per_reservation is None:
        groups = [ids] if ids else []
    else:
        groups = [ids[i:i + per_reservation] for i in range(0, len(ids), per_reservation)]

    response = {
        'FleetId': fleet_id,
        'Instances': [
            {
                'LaunchTemplateAndOverrides': {
                    'Overrides': {'InstanceType': 'c5.large', 'SubnetId': 'subnet-aaa'}
                },
                'Lifecycle': 'on-demand',
                'InstanceIds': group,
            }
            for group in groups
        ],
    }
    if errors:
        response['Errors'] = errors
    return response


def fleet_error(code='InsufficientInstanceCapacity', message='No capacity'):
    return {
        'LaunchTemplateAndOverrides': {
            'Overrides': {'InstanceType': 'c5.large', 'SubnetId': 'subnet-aaa'}
        },
        'Lifecycle': 'on-demand',
        'ErrorCode': code,
        'ErrorMessage': message,
    }


def describe(states, ips=None):
    """A DescribeInstances response.

    states: list of state names, one per instance, indexed the same way as fleet().
            None omits that instance from the response entirely, which is what
            eventual consistency looks like to the plugin.
    ips:    optional list overriding the derived private IPs. None omits the
            PrivateIpAddress key, i.e. running but no IP assigned yet.
    """
    instances = []
    for index, state in enumerate(states):
        if state is None:
            continue
        details = {
            'InstanceId': instance_id(index),
            'State': {'Name': state},
        }
        if ips is None:
            details['PrivateIpAddress'] = private_ip(index)
        elif index < len(ips) and ips[index] is not None:
            details['PrivateIpAddress'] = ips[index]
        instances.append(details)

    return {'Reservations': [{'Instances': instances}] if instances else []}


def all_running(nb_instances):
    return describe(['running'] * nb_instances)
