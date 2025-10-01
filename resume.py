#!/usr/bin/python3
import copy
import json
import re
import socket
import subprocess
import sys
import time

import common


logger, config, partitions = common.get_common('resume')


# Retry in case the request failed because of eventual consistency
def retry(func, *args, **kwargs):
    nb_retry = 1
    MAX_RETRIES = 3
    while True:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if nb_retry <= MAX_RETRIES:
                logger.debug('Failed %s %d time(s): %s', func.__name__, nb_retry, e)
                nb_retry += 1
                time.sleep(nb_retry)
            else:
                raise e


# MPI Support: Wait for all instances to be ready
def wait_for_instances_ready(client, instance_ids, node_names_map, nodegroup, timeout=300):
    """
    Wait for all instances to be running and healthy (MPI support)

    Args:
        client: boto3 EC2 client
        instance_ids: List of instance IDs to wait for
        node_names_map: Dict mapping instance_id -> node_name
        nodegroup: Node group config (for MPIOptions)
        timeout: Max seconds to wait

    Returns:
        dict: {instance_id: {'ip': '10.1.1.50', 'hostname': 'ip-10-1-1-50', 'node_name': 'mpi-compute-0'}}

    Raises:
        TimeoutError: If not all instances ready within timeout
    """
    start_time = time.time()
    ready_instances = {}
    mpi_options = nodegroup.get('MPIOptions', {})
    health_checks = mpi_options.get('HealthChecks', ['network', 'slurmd'])

    logger.info('MPI: Waiting for %d instances to be ready (timeout=%ds, checks=%s)',
                len(instance_ids), timeout, ','.join(health_checks))

    last_progress_log = 0

    while time.time() - start_time < timeout:
        # Check EC2 instance states
        try:
            response = client.describe_instances(InstanceIds=instance_ids)
        except Exception as e:
            logger.warning('Failed to describe instances: %s', e)
            time.sleep(5)
            continue

        # Build map of running instances
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                state = instance['State']['Name']

                if state == 'running' and instance_id not in ready_instances:
                    ip_address = instance.get('PrivateIpAddress')
                    if not ip_address:
                        # Instance running but no IP yet (shouldn't happen, but be defensive)
                        continue

                    hostname = 'ip-%s' % '-'.join(ip_address.split('.'))

                    # Perform health checks
                    if perform_health_checks(ip_address, health_checks):
                        ready_instances[instance_id] = {
                            'ip': ip_address,
                            'hostname': hostname,
                            'node_name': node_names_map[instance_id]
                        }
                        logger.info('MPI: Instance %s ready (%s) [%d/%d]',
                                    instance_id, ip_address,
                                    len(ready_instances), len(instance_ids))

        # Check if all ready
        if len(ready_instances) == len(instance_ids):
            elapsed = time.time() - start_time
            logger.info('MPI: All %d instances ready after %.1fs', len(instance_ids), elapsed)
            return ready_instances

        # Log progress every 30 seconds
        elapsed = int(time.time() - start_time)
        if elapsed - last_progress_log >= 30:
            logger.info('MPI: Progress: %d/%d instances ready (%.1fs elapsed)',
                        len(ready_instances), len(instance_ids), elapsed)
            last_progress_log = elapsed

        time.sleep(5)

    # Timeout reached
    elapsed = time.time() - start_time
    raise TimeoutError(
        'Only %d/%d instances ready after %.1fs' % (len(ready_instances), len(instance_ids), elapsed)
    )


def perform_health_checks(ip_address, checks):
    """
    Perform health checks on a node

    Args:
        ip_address: Node IP to check
        checks: List of check types ['network', 'slurmd', 'nfs']

    Returns:
        bool: True if all checks pass
    """
    if 'network' in checks:
        if not ping_host(ip_address, timeout=3):
            logger.debug('Health check failed for %s: cannot ping', ip_address)
            return False

    if 'slurmd' in checks:
        if not check_port(ip_address, 6818, timeout=3):
            logger.debug('Health check failed for %s: slurmd port not responding', ip_address)
            return False

    # NFS check is expensive (requires SSH), not implemented yet
    if 'nfs' in checks:
        logger.debug('NFS health check not yet implemented, skipping')

    return True


def ping_host(ip_address, timeout=3):
    """Ping a host to verify network connectivity"""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), ip_address],
            capture_output=True,
            timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug('Ping failed for %s: %s', ip_address, e)
        return False


def check_port(ip_address, port, timeout=3):
    """Check if a TCP port is responding"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip_address, port))
        sock.close()
        return True
    except Exception as e:
        logger.debug('Port check failed for %s:%d: %s', ip_address, port, e)
        return False


# Retrieve the list of hosts to resume
try:
    hostlist = sys.argv[1]
    logger.info('Hostlist: %s' %hostlist)
except:
    logger.critical('Missing hostlist argument')
    sys.exit(1)

# Expand the hoslist and retrieve a list of node names
expanded_hostlist = common.expand_hostlist(hostlist)
logger.debug('Expanded hostlist: %s' %', '.join(expanded_hostlist))

# Parse the expanded hostlist
nodes_to_resume = common.parse_node_names(expanded_hostlist)
logger.debug('Nodes to resume: %s', json.dumps(nodes_to_resume, indent=4))

for partition_name, nodegroups in nodes_to_resume.items():
    for nodegroup_name, node_ids in nodegroups.items():
        
        nb_nodes_to_resume = len(node_ids)
        nodegroup = common.get_partition_nodegroup(partition_name, nodegroup_name)
        
        # Ignore if the partition and the node group are not in partitions.json
        if nodegroup is None:
            logger.warning('Skipping partition=%s nodegroup=%s: not in partition.json' %(partition_name, nodegroup_name))
            continue
        
        client = common.get_ec2_client(nodegroup)

        # Create a dict for the EC2 CreateFleet request
        request_fleet = {
            'LaunchTemplateConfigs': [
                {
                    'LaunchTemplateSpecification': nodegroup['LaunchTemplateSpecification'],
                    'Overrides': []
                }
            ],
            'TargetCapacitySpecification': {
                'TotalTargetCapacity': nb_nodes_to_resume,
                'DefaultTargetCapacityType': nodegroup['PurchasingOption']
            },
            'Type': 'instant'
        }
            
        # Populate on-demand options
        if 'OnDemandOptions' in nodegroup:
            request_fleet['OnDemandOptions'] = nodegroup['OnDemandOptions']
        
        # Populate spot options
        if 'SpotOptions' in nodegroup:
            request_fleet['SpotOptions'] = nodegroup['SpotOptions']
            request_fleet['SpotOptions']['InstanceInterruptionBehavior'] = 'terminate'
            
        # Populate launch configuration overrides. Duplicate overrides for each subnet
        for override in nodegroup['LaunchTemplateOverrides']:
            for subnet in nodegroup['SubnetIds']:
                override_copy = copy.deepcopy(override)
                override_copy['SubnetId'] = subnet
                override_copy['WeightedCapacity'] = 1

                # MPI Support: Add placement group if specified
                if 'PlacementGroupName' in nodegroup:
                    override_copy['Placement'] = {
                        'GroupName': nodegroup['PlacementGroupName']
                    }
                    logger.debug('Using placement group: %s', nodegroup['PlacementGroupName'])

                request_fleet['LaunchTemplateConfigs'][0]['Overrides'].append(override_copy)

        # Create an EC2 fleet
        try:
            logger.debug('EC2 CreateFleet request: %s' %json.dumps(request_fleet, indent=4))
            response_fleet = client.create_fleet(**request_fleet)
            logger.debug('EC2 CreateFleet response: %s' %json.dumps(response_fleet, indent=4))
        except Exception as e:
            logger.error('Failed to launch nodes for partition=%s and nodegroup=%s - %s' %(partition_name, nodegroup_name, e))
            continue

        # Check if MPI support is enabled
        enable_mpi = nodegroup.get('EnableMPISupport', False)
        mpi_options = nodegroup.get('MPIOptions', {})

        if enable_mpi and nb_nodes_to_resume > 1:
            # MPI Mode: Synchronous launch - wait for all nodes to be ready
            logger.info('MPI mode enabled: launching %d nodes synchronously', nb_nodes_to_resume)

            # Collect all instance IDs
            all_instance_ids = []
            node_names_map = {}
            node_id_index = 0

            for instance in response_fleet['Instances']:
                for instance_id in instance['InstanceIds']:
                    node_id = node_ids[node_id_index]
                    node_name = common.get_node_name(partition_name, nodegroup_name, node_id)
                    all_instance_ids.append(instance_id)
                    node_names_map[instance_id] = node_name
                    node_id_index += 1

            # Wait for all instances to be ready
            timeout = mpi_options.get('TimeoutSeconds', 300)
            try:
                ready_instances = wait_for_instances_ready(
                    client,
                    all_instance_ids,
                    node_names_map,
                    nodegroup,
                    timeout=timeout
                )
            except TimeoutError as e:
                logger.error('MPI node launch failed: %s', e)
                logger.warning('Terminating %d instances due to timeout', len(all_instance_ids))
                try:
                    client.terminate_instances(InstanceIds=all_instance_ids)
                except Exception as term_error:
                    logger.error('Failed to terminate instances: %s', term_error)
                continue

            # Tag and update Slurm for all ready instances
            for instance_id, instance_info in ready_instances.items():
                ip_address = instance_info['ip']
                hostname = instance_info['hostname']
                node_name = instance_info['node_name']

                logger.info('MPI: Configuring node %s %s %s', node_name, instance_id, ip_address)

                # Tag instance
                tags = [
                    {
                        'Key': 'Name',
                        'Value': '{node_name}'
                    }
                ]
                if 'Tags' in nodegroup:
                    tags += nodegroup['Tags']

                # Replace sequences
                sequences = (
                    ('{ip_address}', ip_address),
                    ('{node_name}', node_name),
                    ('{hostname}', hostname)
                )
                for tag in tags:
                    for sequence in sequences:
                        tag['Value'] = tag['Value'].replace(*sequence)

                try:
                    request_tags = {
                        'Resources': [instance_id],
                        'Tags': tags
                    }
                    retry(client.create_tags, **request_tags)
                    logger.debug('Tagged node %s', node_name)
                except Exception as e:
                    logger.error('Failed to tag node %s - %s', node_name, e)

                # Update Slurm
                try:
                    slurm_param = 'nodeaddr=%s nodehostname=%s' %(ip_address, hostname)
                    common.update_node(node_name, slurm_param)
                    logger.debug('Updated node information in Slurm %s', node_name)
                except Exception as e:
                    logger.error('Failed to update node information in Slurm %s - %s', node_name, e)

            logger.info('MPI: All %d nodes configured and ready', len(ready_instances))

        else:
            # Standard Mode: Asynchronous launch (existing behavior)
            if enable_mpi and nb_nodes_to_resume == 1:
                logger.debug('MPI mode enabled but only 1 node requested, using standard launch')

            # This variable will be used as an incremental index of node_ids
            node_id_index = 0

            # For all instances that were successfully launched
            for instance in response_fleet['Instances']:

                # Retrieve additional instance details
                try:
                    response_describe = retry(client.describe_instances, InstanceIds=instance['InstanceIds'])
                except Exception as e:
                    logger.error('Failed to describe instances %s: %s' %(', '.join(instance['InstanceIds']), e))
                    continue

                # For each instance that was successfully launched
                for instance_id in instance['InstanceIds']:
                    node_id = node_ids[node_id_index]
                    node_id_index += 1
                    node_name = common.get_node_name(partition_name, nodegroup_name, node_id)

                    # Isolate details for the current instance
                    for reservation in response_describe['Reservations']:
                        for instance_details in reservation['Instances']:
                            if instance_details['InstanceId'] == instance_id:
                                ip_address = instance_details['PrivateIpAddress']
                                hostname = 'ip-%s' %'-'.join(ip_address.split('.'))

                    logger.info('Launched node %s %s %s' %(node_name, instance_id, ip_address))

                    # Tag the instance
                    tags = [
                        {
                            'Key': 'Name',
                            'Value': '{node_name}'
                        }
                    ]
                    if 'Tags' in nodegroup:
                        tags += nodegroup['Tags']

                    # Replace the following sequences with context values
                    # For example, replace {ip_address} with the private IP address
                    sequences = (
                        ('{ip_address}', ip_address),
                        ('{node_name}', node_name),
                        ('{hostname}', hostname)
                    )
                    for tag in tags:
                        for sequence in sequences:
                          tag['Value'] = tag['Value'].replace(*sequence)

                    try:
                        request_tags = {
                            'Resources': [instance_id],
                            'Tags': tags
                        }
                        retry(client.create_tags, **request_tags)
                        logger.debug('Tagged node %s: %s' %(node_name, json.dumps(request_tags, indent=4)))
                    except Exception as e:
                        logger.error('Failed to tag node %s - %s' %(node_name, e))
                        continue

                    # Update node information in Slurm
                    try:
                        slurm_param = 'nodeaddr=%s nodehostname=%s' %(ip_address, hostname)
                        common.update_node(node_name, slurm_param)
                        logger.debug('Updated node information in Slurm %s' %node_name)
                    except Exception as e:
                        logger.error('Failed to update node information in Slurm %s - %s' %(node_name, e))

        # Log how many nodes failed to launch
        nb_failed_nodes = nb_nodes_to_resume - node_id_index
        if nb_failed_nodes > 0:
            logger.warning('Failed to launch %s nodes' %nb_failed_nodes)

        # Log EC2 fleet errors
        error_codes = []
        for error in response_fleet['Errors']:
            override = error['LaunchTemplateAndOverrides']['Overrides']
            logger.debug('EC2 Fleet error - %s - Instance type: %s Subnet: %s Lifecycle: %s' %(
                error['ErrorMessage'], override['InstanceType'], override['SubnetId'],
                error['Lifecycle']
            ))
            if not error['ErrorCode'] in error_codes:
                error_codes.append(error['ErrorCode'])

        if len(error_codes) > 0:
            logger.warning('EC2 Fleet error codes: %s' %', '.join(error_codes))
