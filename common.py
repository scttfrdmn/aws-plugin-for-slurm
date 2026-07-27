import json
import logging
import os
import re
import subprocess
import sys

import boto3


dir_path = os.path.dirname(os.path.realpath(__file__))  # Folder where resides the Python files

logger = None  # Global variable for the logging.Logger object
config = None  # Global variable for the config parameters
partitions = None  # Global variable that stores partitions details

# Default MPIOptions.TimeoutSeconds. Defined here rather than in resume.py because
# validation needs it to check TimeoutSeconds against ResumeTimeout.
DEFAULT_LAUNCH_TIMEOUT = 300


# Create and return a logging.Logger object
# - scriptname: name of the module
# - levelname: log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
# - filename: location of the log file
def get_logger(scriptname, levelname, filename):
    
    logger = logging.getLogger(scriptname)
    
    # Update log level
    log_levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    logger.setLevel(log_levels.get(levelname, logging.DEBUG))
    
    # Create a console handler
    sh = logging.StreamHandler()
    sh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    sh.setFormatter(sh_formatter)
    logger.addHandler(sh)
    
    # Create a file handler
    fh = logging.FileHandler(filename)
    fh_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)
    
    return logger
    

# Validate the structure of the config.json file content
# - data: dict loaded from config.json
def validate_config(data):
    
    assert 'LogLevel' in data, 'Missing "LogLevel" in root'
    assert data['LogLevel'] in ('CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'), 'root["LogLevel"] is an invalid value'
    
    assert 'LogFileName' in data, 'Missing "LogFileName" in root'
    
    assert 'SlurmBinPath' in data, 'Missing "SlurmBinPath" in root'
    
    assert 'SlurmConf' in data, 'Missing "SlurmConf" in root'
    slurm_conf = data['SlurmConf']
    assert isinstance(slurm_conf, dict), 'root["SlurmConf"] is not a dict'
    
    assert 'PrivateData' in slurm_conf, 'Missing "PrivateData" in root["SlurmConf"]'
    assert 'ResumeProgram' in slurm_conf, 'Missing "ResumeProgram" in root["SlurmConf"]'
    assert 'SuspendProgram' in slurm_conf, 'Missing "SuspendProgram" in root["SlurmConf"]'
    assert 'ResumeRate' in slurm_conf, 'Missing "ResumeRate" in root["SlurmConf"]'
    assert 'SuspendRate' in slurm_conf, 'Missing "SuspendRate" in root["SlurmConf"]'
    assert 'ResumeTimeout' in slurm_conf, 'Missing "ResumeTimeout" in root["SlurmConf"]'
    assert 'SuspendTime' in slurm_conf, 'Missing "SuspendTime" in root["SlurmConf"]'
    assert 'TreeWidth' in slurm_conf, 'Missing "TreeWidth" in root["SlurmConf"]'
    
    
# Validate the structure of the partitions.json file content
# - data: dict loaded from partitions.json
def validate_partitions(data):
    
    assert 'Partitions' in data, 'Missing "Partitions" in root'
    assert isinstance(data['Partitions'], list), 'root["Partitions"] is not an array'
    
    for i_partition, partition in enumerate(data['Partitions']):
        assert 'PartitionName' in partition, 'Missing "PartitionName" in root["Partitions"][%s]' %i_partition
        assert re.match('^[a-zA-Z0-9]+$', partition['PartitionName']), 'root["Partitions"][%s]["PartitionName"] does not match ^[a-zA-Z0-9]+$' %i_partition
        
        assert 'NodeGroups' in partition, 'Missing "NodeGroups" in root["Partitions"][%s]' %i_partition
        assert isinstance(partition['NodeGroups'], list), 'root["Partitions"][%s]["NodeGroups"] is not an array' %i_partition
        
        for i_nodegroup, nodegroup in enumerate(partition['NodeGroups']):
            assert 'NodeGroupName' in nodegroup, 'Missing "NodeGroupName" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert re.match('^[a-zA-Z0-9]+$', nodegroup['NodeGroupName']), 'root["Partitions"][%s]["NodeGroups"][%s]["NodeGroupName"] does not match ^[a-zA-Z0-9]+$' %(i_partition, i_nodegroup)
            
            assert 'MaxNodes' in nodegroup, 'Missing "MaxNodes" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert isinstance(nodegroup['MaxNodes'], int), 'root["Partitions"][%s]["NodeGroups"][%s]["MaxNodes"] is not a number' %(i_partition, i_nodegroup)
            
            assert 'Region' in nodegroup, 'Missing "Region" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            
            assert 'SlurmSpecifications' in nodegroup, 'Missing "SlurmSpecifications" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert isinstance(nodegroup['SlurmSpecifications'], dict), 'root["Partitions"][%s]["NodeGroups"][%s]["SlurmSpecifications"] is not a dict' %(i_partition, i_nodegroup)
            
            assert 'PurchasingOption' in nodegroup, 'Missing "PurchasingOption" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert nodegroup['PurchasingOption'] in ('spot', 'on-demand'), 'root["Partitions"][%s]["NodeGroups"][%s]["PurchasingOption"] must be spot or on-demand' %(i_partition, i_nodegroup)
            
            assert 'LaunchTemplateSpecification' in nodegroup, 'Missing "LaunchTemplateSpecification" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert isinstance(nodegroup['LaunchTemplateSpecification'], dict), 'root["Partitions"][%s]["NodeGroups"][%s]["LaunchTemplateSpecification"] is not a dict' %(i_partition, i_nodegroup)
            
            assert 'LaunchTemplateOverrides' in nodegroup, 'Missing "LaunchTemplateOverrides" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert isinstance(nodegroup['LaunchTemplateOverrides'], list), 'root["Partitions"][%s]["NodeGroups"][%s]["LaunchTemplateOverrides"] is not a dict' %(i_partition, i_nodegroup)
            
            assert 'SubnetIds' in nodegroup, 'Missing "SubnetIds" in root["Partitions"][%s]["NodeGroups"][%s]' %(i_partition, i_nodegroup)
            assert isinstance(nodegroup['SubnetIds'], list), 'root["Partitions"][%s]["NodeGroups"][%s]["SubnetIds"] is not a dict' %(i_partition, i_nodegroup)

            # Validate synchronous launch fields (optional, see docs/mpi-support.md)
            if 'EnableMPISupport' in nodegroup:
                assert isinstance(nodegroup['EnableMPISupport'], bool), \
                    'root["Partitions"][%s]["NodeGroups"][%s]["EnableMPISupport"] must be boolean' %(i_partition, i_nodegroup)

            if 'PlacementGroupName' in nodegroup:
                assert isinstance(nodegroup['PlacementGroupName'], str), \
                    'root["Partitions"][%s]["NodeGroups"][%s]["PlacementGroupName"] must be string' %(i_partition, i_nodegroup)

            if 'MPIOptions' in nodegroup:
                assert isinstance(nodegroup['MPIOptions'], dict), \
                    'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"] must be dict' %(i_partition, i_nodegroup)

                mpi_options = nodegroup['MPIOptions']

                if 'WaitForAllNodes' in mpi_options:
                    assert isinstance(mpi_options['WaitForAllNodes'], bool), \
                        'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"]["WaitForAllNodes"] must be boolean' %(i_partition, i_nodegroup)

                if 'TimeoutSeconds' in mpi_options:
                    assert isinstance(mpi_options['TimeoutSeconds'], int), \
                        'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"]["TimeoutSeconds"] must be integer' %(i_partition, i_nodegroup)
                    assert mpi_options['TimeoutSeconds'] > 0, \
                        'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"]["TimeoutSeconds"] must be positive' %(i_partition, i_nodegroup)

                if 'HealthChecks' in mpi_options:
                    assert isinstance(mpi_options['HealthChecks'], list), \
                        'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"]["HealthChecks"] must be array' %(i_partition, i_nodegroup)
                    valid_checks = ['network', 'slurmd']
                    for check in mpi_options['HealthChecks']:
                        assert check in valid_checks, \
                            'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"]["HealthChecks"] contains invalid check: %s (valid: %s)' \
                            %(i_partition, i_nodegroup, check, ','.join(valid_checks))

                if 'RequirePlacementGroup' in mpi_options:
                    assert isinstance(mpi_options['RequirePlacementGroup'], bool), \
                        'root["Partitions"][%s]["NodeGroups"][%s]["MPIOptions"]["RequirePlacementGroup"] must be boolean' %(i_partition, i_nodegroup)

        if 'PartitionOptions' in partition:
            assert isinstance(partition['PartitionOptions'], dict), 'root["Partitions"][%s]["PartitionOptions"] is not a dict' %(i_partition)


# Warn about combinations of config.json and partitions.json that are individually
# valid but interact badly at runtime. These are warnings, not assertions: the
# authoritative slurm.conf may differ from config.json, and refusing to launch would
# be worse than a noisy log.
# - config_data: dict loaded from config.json
# - partitions_data: dict loaded from partitions.json
def warn_on_conflicting_settings(config_data, partitions_data):

    slurm_conf = config_data.get('SlurmConf', {})
    resume_timeout = slurm_conf.get('ResumeTimeout')
    resume_rate = slurm_conf.get('ResumeRate')

    for partition in partitions_data.get('Partitions', []):
        for nodegroup in partition.get('NodeGroups', []):
            if not nodegroup.get('EnableMPISupport', False):
                continue

            name = '%s/%s' %(partition.get('PartitionName'), nodegroup.get('NodeGroupName'))
            mpi_options = nodegroup.get('MPIOptions', {})
            if not mpi_options.get('WaitForAllNodes', True):
                continue

            # resume.py blocks for up to TimeoutSeconds while Slurm independently
            # counts down ResumeTimeout. Without headroom, Slurm marks the nodes DOWN
            # while the plugin is still waiting for them.
            timeout_seconds = mpi_options.get('TimeoutSeconds', DEFAULT_LAUNCH_TIMEOUT)
            if isinstance(resume_timeout, int) and timeout_seconds + 120 > resume_timeout:
                logger.warning(
                    'Node group %s: MPIOptions.TimeoutSeconds (%d) leaves too little '
                    'headroom under ResumeTimeout (%d). Slurm may mark nodes DOWN while '
                    'resume.py is still waiting. Set ResumeTimeout to at least %d.',
                    name, timeout_seconds, resume_timeout, timeout_seconds + 120
                )

            # Slurm calls ResumeProgram once per batch of at most ResumeRate nodes, and
            # each invocation only sees its own subset, so a larger allocation is split
            # into independent waits and the all-or-nothing guarantee applies per batch.
            max_nodes = nodegroup.get('MaxNodes')
            if isinstance(resume_rate, int) and isinstance(max_nodes, int) \
                    and resume_rate < max_nodes:
                logger.warning(
                    'Node group %s: ResumeRate (%d) is below MaxNodes (%d). Allocations '
                    'larger than %d nodes are split across several resume.py invocations, '
                    'so all-or-nothing launch applies per batch, not per job.',
                    name, resume_rate, max_nodes, resume_rate
                )


# Create and return logger, config, and partitions variables
def get_common(scriptname):
    
    global logger
    global config
    global partitions
    
    # Load configuration parameters from ./config.json and merge with default values
    try:
        config_filename = '%s/config.json' %dir_path
        with open(config_filename, 'r') as f:
            config = json.load(f)
    except Exception as e:
        config = {'JsonLoadError': str(e)}

    # Populate default values if unspecified
    if not 'LogFileName' in config:
        config['LogFileName'] = '%s/aws_plugin.log' %dir_path
    if not 'LogLevel' in config:
        config['LogLevel'] = 'DEBUG'

    # Make sure that SlurmBinPath ends with a /
    if 'SlurmBinPath' in config and not config['SlurmBinPath'].endswith('/'):
        config['SlurmBinPath'] += '/'
    
    # Create a logger
    logger = get_logger(scriptname, config['LogLevel'], config['LogFileName'])
    logger.debug('Config: %s' %json.dumps(config, indent=4))
    
    # Validate the structure of config.json
    if 'JsonLoadError' in config:
        logger.critical('Failed to load %s - %s' %(config['LogFileName'], config['JsonLoadError']))
        sys.exit(1)
    try:
        validate_config(config)
    except Exception as e:
        logger.critical('File config.json is invalid - %s' %e)
        sys.exit(1)

    # Load partitions details from ./partitions.json
    partitions_filename = '%s/partitions.json' %dir_path
    try:
        with open(partitions_filename, 'r') as f:
            partitions_json = json.load(f)
    except Exception as e:
        logger.critical('Failed to load %s - %s' %(partitions_filename, e))
        sys.exit(1)
        
    # Validate the structure of partitions.json
    try:
        validate_partitions(partitions_json)
    except Exception as e:
        logger.critical('File partition.json is invalid - %s' %e)
        sys.exit(1)
    finally:
        partitions = partitions_json['Partitions']
        logger.debug('Partitions: %s' %json.dumps(partitions_json, indent=4))

    # Warn about settings that are individually valid but conflict at runtime. Never
    # fatal - a bad warning must not stop nodes from launching.
    try:
        warn_on_conflicting_settings(config, partitions_json)
    except Exception as e:
        logger.debug('Could not check for conflicting settings - %s' %e)
    
    return logger, config, partitions


# Return the name of a node [partition_name]-[nodegroup_name][id]
# - partition: can either be a string, or a dict with dict['PartitionName'] = partition_name
# - nodegroup: can either be a string, or a dict with dict['NodeGroupName'] = nodegroup_name
# - id: optional id
def get_node_name(partition, nodegroup, node_id=''):
    
    if isinstance(partition, dict):
        partition_name = partition['PartitionName']
    else:
        partition_name = partition
        
    if isinstance(nodegroup, dict):
        nodegroup_name = nodegroup['NodeGroupName']
    else:
        nodegroup_name = nodegroup
    
    if node_id == '':
        return '%s-%s' %(partition_name, nodegroup_name)
    else:
        return '%s-%s-%s' %(partition_name, nodegroup_name, node_id)
    

# Return the name of a node [partition_name]-[nodegroup_name][id]
# - partition: can either be a string, or a dict with dict['PartitionName'] = partition_name
# - nodegroup: can either be a string, or a dict with dict['NodeGroupName'] = nodegroup_name
# - nb_nodes: optional number of nodes
def get_node_range(partition, nodegroup, nb_nodes=None):
    
    if nb_nodes is None:
        nb_nodes = nodegroup['MaxNodes']
        
    if nb_nodes > 1:
        return '%s-[0-%s]' %(get_node_name(partition, nodegroup), nb_nodes-1)
    else:
        return '%s-0' %(get_node_name(partition, nodegroup))


# Run scontrol and return output
# - command: name of the command such as scontrol
# - arguments: array
def run_scommand(command, arguments):
    
    scommand_path = '%s%s' %(config['SlurmBinPath'], command)
    cmd = [scommand_path] + arguments
    logger.debug('Command %s: %s' %(command, ' '.join(cmd)))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    lines = proc.communicate()[0].splitlines()
    return [line.decode() for line in lines]


# Use 'scontrol show hostnames' to expand the hostlist and return a list of node names
# - hostlist: argument passed to SuspendProgram or ResumeProgram
def expand_hostlist(hostlist):
    
    try:
        arguments = ['show', 'hostnames', hostlist]
        return run_scommand('scontrol', arguments)
    except Exception as e:
        logger.critical('Failed to expand hostlist - %s' %e)
        sys.exit(1)


# Take a list of node names in input and return a dict with result[partition_name][nodegroup_name] = list of node ids
def parse_node_names(node_names):
    result = {}
    for node_name in node_names:
        
        # For each node: extract partition name, node group name and node id
        pattern = '^([a-zA-Z0-9]+)-([a-zA-Z0-9]+)-([0-9]+)$'
        match = re.match(pattern, node_name)
        if match:
            partition_name, nodegroup_name, node_id = match.groups()
            
            # Add to result
            if not partition_name in result:
                result[partition_name] = {}
            if not nodegroup_name in result[partition_name]:
                result[partition_name][nodegroup_name] = []
            result[partition_name][nodegroup_name].append(node_id)
    
    return result


# Return a pointer in partitions to a specific partition and node group
def get_partition_nodegroup(partition_name, nodegroup_name):
    
    for partition in partitions:
        if partition['PartitionName'] == partition_name:
            for nodegroup in partition['NodeGroups']:
                if nodegroup['NodeGroupName'] == nodegroup_name:
                    return nodegroup
    
    # Return None if it does not exist
    return None


# Use 'scontrol update node' to update nodes
def update_node(node_name, parameters):
    
    parameters_split = parameters.split(' ')
    arguments = ['update', 'nodename=%s' %node_name] + parameters_split
    run_scommand('scontrol', arguments)
    
    
# Call sinfo and return node status for a list of nodes
def get_node_state(hostlist):
    
    try:
        cmd = [scontrol_path, '-n', ','.join(hostlist), '-N', '-o', '"%N %t"']
        return run_scommand('sinfo', arguments)
    except Exception as e:
        logger.critical('Failed to retrieve node state - %s' %e)
        sys.exit(1)


# Return boto3 client
def get_ec2_client(nodegroup):
    
    if 'ProfileName' in nodegroup:
        try:
            session = boto3.session.Session(region_name=nodegroup['Region'], profile_name=nodegroup['ProfileName'])
            return session.client('ec2')
        except Exception as e:
            logger.critical('Failed to create a EC2 client - %s' %e)
            sys.exit(1)
    else:
        return boto3.client('ec2', region_name=nodegroup['Region'])
