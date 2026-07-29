# Configuration Reference

This document provides detailed information about the plugin configuration files.

## Overview

The plugin uses two JSON configuration files that must reside in the same folder as the Python scripts:

- `config.json` - Plugin and Slurm configuration parameters
- `partitions.json` - Node groups and partition specifications

## config.json

This JSON file specifies the plugin and Slurm configuration parameters.

### Schema

```json
{
   "LogLevel": "STRING",
   "LogFileName": "STRING",
   "SlurmBinPath": "STRING",
   "SlurmConf": {
      "PrivateData": "STRING",
      "ResumeProgram": "STRING",
      "SuspendProgram": "STRING",
      "ResumeRate": INT,
      "SuspendRate": INT,
      "ResumeTimeout": INT,
      "SuspendTime": INT,
      "TreeWidth": INT,
      "GresTypes": "STRING"
      ...
   }
}
```

### Parameters

#### LogLevel
- **Type**: String
- **Values**: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`
- **Default**: `DEBUG`
- **Description**: Logging level for the plugin

#### LogFileName
- **Type**: String
- **Default**: `PLUGIN_PATH/aws_plugin.log`
- **Description**: Full path to the log file location

#### SlurmBinPath
- **Type**: String
- **Required**: Yes
- **Description**: Full path to the folder containing Slurm binaries (`scontrol`, `sinfo`, etc.)
- **Example**: `/slurm/bin`

#### SlurmConf

These attributes are used by `generate_conf.py` to generate content that must be appended to the Slurm configuration file.

##### PrivateData
- **Type**: String
- **Required**: Yes
- **Value**: Must be `CLOUD`
- **Description**: Ensures idle EC2 compute nodes are returned by Slurm command outputs such as `sinfo`

##### ResumeProgram
- **Type**: String
- **Required**: Yes
- **Description**: Full path to `resume.py`
- **Example**: `/slurm/etc/aws/resume.py`

##### SuspendProgram
- **Type**: String
- **Required**: Yes
- **Description**: Full path to `suspend.py`
- **Example**: `/slurm/etc/aws/suspend.py`

##### ResumeRate
- **Type**: Integer
- **Required**: Yes
- **Recommended**: `100`
- **Description**: Maximum number of EC2 instances Slurm can launch per minute. Setting this too high may hit EC2 request rate limits.

##### SuspendRate
- **Type**: Integer
- **Required**: Yes
- **Recommended**: `100`
- **Description**: Maximum number of EC2 instances Slurm can terminate per minute. Setting this too high may hit EC2 request rate limits.

##### ResumeTimeout
- **Type**: Integer (seconds)
- **Required**: Yes
- **Description**: Maximum time permitted between when a node resume request is issued and when the node is available. Consider instance launch time plus bootstrap script execution time.

##### SuspendTime
- **Type**: Integer (seconds)
- **Required**: Yes
- **Description**: Nodes become eligible for power saving mode after being idle or down for this duration. Should be at least as large as `SuspendTimeout` (default 30s) plus `ResumeTimeout`.

##### TreeWidth
- **Type**: Integer
- **Required**: Yes
- **Recommended**: `60000`
- **Description**: Slurm communication tree width. Refer to Slurm documentation for details.

##### GresTypes
- **Type**: String
- **Optional**: Yes
- **Example**: `"gpu"` or `"gpu,mps"`
- **Description**: Comma-separated list of generic resource types to enable. Use `"gpu"` if any node groups will have GPU resources. This must be specified if you use `Gres` in `SlurmSpecifications`.

### Example config.json

```json
{
   "LogLevel": "INFO",
   "LogFileName": "/var/log/slurm/aws.log",
   "SlurmBinPath": "/slurm/bin",
   "SlurmConf": {
      "PrivateData": "CLOUD",
      "ResumeProgram": "/slurm/etc/aws/resume.py",
      "SuspendProgram": "/slurm/etc/aws/suspend.py",
      "ResumeRate": 100,
      "SuspendRate": 100,
      "ResumeTimeout": 300,
      "SuspendTime": 350,
      "TreeWidth": 60000,
      "GresTypes": "gpu"
   }
}
```

## partitions.json

This JSON file specifies the groups of nodes and associated partitions that Slurm can launch in AWS.

### Schema

```json
{
   "Partitions": [
      {
         "PartitionName": "STRING",
         "NodeGroups": [
            {
               "NodeGroupName": "STRING",
               "MaxNodes": INT,
               "Region": "STRING",
               "ProfileName": "STRING",
               "SlurmSpecifications": {
                  "NodeSpec1": "STRING",
                  "NodeSpec2": "STRING",
                  ...
               },
               "PurchasingOption": "spot|on-demand",
               "OnDemandOptions": DICT,
               "SpotOptions": DICT,
               "LaunchTemplateSpecification": DICT,
               "LaunchTemplateOverrides": ARRAY,
               "SubnetIds": [ "STRING" ],
               "Tags": [
                  {
                     "Key": "STRING",
                     "Value": "STRING"
                  }
               ]
            }
         ],
         "PartitionOptions": {
            "Option1": "STRING",
            "Option2": "STRING"
         }
      }
   ]
}
```

### Parameters

#### Partitions
- **Type**: Array of partition objects
- **Description**: List of Slurm partitions to configure

##### PartitionName
- **Type**: String
- **Pattern**: `^[a-zA-Z0-9]+$`
- **Description**: Name of the partition

##### NodeGroups
- **Type**: Array of node group objects
- **Description**: List of node groups for this partition. A node group is a set of nodes that share the same specifications.

###### NodeGroupName
- **Type**: String
- **Pattern**: `^[a-zA-Z0-9]+$`
- **Description**: Name of the node group

###### MaxNodes
- **Type**: Integer
- **Description**: Maximum number of nodes Slurm can launch for this node group. `generate_conf.py` will create node names: `[partition_name]-[nodegroup_name]-[0-(max_nodes-1)]`

###### Region
- **Type**: String
- **Required**: Yes
- **Description**: AWS region where EC2 instances will be launched
- **Example**: `us-east-1`

###### ProfileName
- **Type**: String
- **Optional**: Yes
- **Description**: AWS CLI profile name for authentication. If not specified, uses the default profile or EC2 metadata credentials.

###### SlurmSpecifications
- **Type**: Object (key-value pairs)
- **Description**: Slurm configuration attributes for this node group. These are added to the node definition in `slurm.conf`.
- **Example**: `{"CPUs": 4, "Features": "us-east-1a"}` generates `CPUs=4 Features=us-east-1a`
- **GPU Support**: Use the `Gres` key to specify GPU resources (see [GPU Configuration](#gpu-configuration) below)

###### PurchasingOption
- **Type**: String
- **Values**: `spot` or `on-demand`
- **Required**: Yes
- **Description**: Instance purchasing model

###### OnDemandOptions
- **Type**: Object
- **Required**: When `PurchasingOption` is `on-demand`
- **Description**: Configuration matching the [EC2 CreateFleet API OnDemandOptions](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Client.create_fleet)

###### SpotOptions
- **Type**: Object
- **Required**: When `PurchasingOption` is `spot`
- **Description**: Configuration matching the [EC2 CreateFleet API SpotOptions](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Client.create_fleet)

###### LaunchTemplateSpecification
- **Type**: Object
- **Required**: Yes
- **Description**: Specifies the launch template to use. Format matches the [EC2 CreateFleet API LaunchTemplateSpecification](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Client.create_fleet)

###### LaunchTemplateOverrides
- **Type**: Array
- **Required**: Yes
- **Description**: Specifies instance types and other overrides. Format matches the [EC2 CreateFleet API LaunchTemplateOverrides](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Client.create_fleet)
- **Note**: Do **not** populate the `SubnetId` field in template overrides. Use the `SubnetIds` parameter instead.

###### SubnetIds
- **Type**: Array of strings
- **Required**: Yes
- **Description**: List of subnets where EC2 instances can be launched. If multiple subnets are specified, they must be in different availability zones to avoid the error: "The fleet configuration contains duplicate instance pools"

###### Tags
- **Type**: Array of tag objects
- **Optional**: Yes
- **Description**: Tags applied to EC2 instances launched for this node group
- **Special Tags**:
  - `Name` - Automatically added with value `[partition_name]-[nodegroup_name]-[id]`. Do not override this tag as `suspend.py` uses it to find instances.
  - Template variables: Use `{ip_address}`, `{node_name}`, or `{hostname}` in tag values for dynamic substitution

###### PlacementGroupName
- **Type**: String
- **Optional**: Yes
- **Added in**: v3.1
- **Description**: Name of an existing EC2 placement group to launch instances into. Overrides any `Placement` set in the launch template. A cluster placement group cannot span availability zones, so use a single subnet in `SubnetIds` when this is set.

###### EnableMPISupport
- **Type**: Boolean
- **Optional**: Yes
- **Default**: `false`
- **Added in**: v3.1
- **Description**: Enable all-or-nothing launch for this node group. `resume.py` waits for the full allocation to be ready and terminates it if any node is missing or unhealthy, rather than registering a partial allocation. Only affects multi-node requests. See [MPI and Tightly-Coupled Workloads](mpi-support.md).

###### MPIOptions
- **Type**: Object
- **Optional**: Yes
- **Added in**: v3.1
- **Description**: Tuning for all-or-nothing launch. Ignored unless `EnableMPISupport` is `true`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `WaitForAllNodes` | Boolean | `true` | Set `false` to launch asynchronously (v2 behavior) while keeping other settings |
| `TimeoutSeconds` | Integer | `300` | Seconds to wait for the full allocation before terminating it. **Must be well below `ResumeTimeout`** |
| `HealthChecks` | Array | `["slurmd"]` | Readiness checks: `slurmd` (TCP 6818), `network` (ICMP). `[]` disables |
| `RequirePlacementGroup` | Boolean | `false` | Skip the launch entirely if `PlacementGroupName` is not set |

**Warning**: `network` uses ICMP and is **not** enabled by default. If your security group blocks ICMP, enabling it causes every node to fail its check and the entire healthy allocation to be terminated at the timeout.

`slurmd` and `network` are the only valid values; anything else is rejected at startup. There is deliberately no `nfs` check — the headnode cannot see a compute node's mount table, so gate `slurmd` on the mount in the node's own boot instead and the `slurmd` check covers it. See [MPI Support - There is no `nfs` readiness check](mpi-support.md#there-is-no-nfs-readiness-check-gate-slurmd-instead).

**Warning**: `resume.py` blocks for up to `TimeoutSeconds`. Slurm counts down `ResumeTimeout` independently, so if `TimeoutSeconds >= ResumeTimeout` Slurm marks nodes `DOWN` while the plugin is still waiting. Keep `TimeoutSeconds <= ResumeTimeout - 120`.

##### PartitionOptions
- **Type**: Object (key-value pairs)
- **Optional**: Yes
- **Description**: Slurm configuration attributes for the partition. These are added to the partition definition in `slurm.conf`.

## GPU Configuration

The plugin supports Generic Resources (GRES), particularly GPU resources, through Slurm's GRES framework.

### Enabling GPU Support

1. **Add `GresTypes` to config.json:**
   ```json
   {
      "SlurmConf": {
         "GresTypes": "gpu"
      }
   }
   ```

2. **Specify GPU resources in node group `SlurmSpecifications`:**

   The `Gres` field uses the format: `gpu:count` or `gpu:type:count`

   **Examples:**
   - `"Gres": "gpu:1"` - One GPU of any type
   - `"Gres": "gpu:4"` - Four GPUs
   - `"Gres": "gpu:a100:8"` - Eight A100 GPUs
   - `"Gres": "gpu:v100:2"` - Two V100 GPUs

3. **The plugin automatically generates `gres.conf`:**
   - Single GPU: `File=/dev/nvidia[0]`
   - Multiple GPUs: `File=/dev/nvidia[0-N]`

### GPU Instance Types

Common AWS GPU instance families:
- **P4/P5**: A100 GPUs (ml workloads, high performance)
- **P3**: V100 GPUs (deep learning)
- **G5**: A10G GPUs (graphics, ML inference)
- **G4dn**: T4 GPUs (cost-effective ML inference)

### GPU Configuration Example

```json
{
   "NodeGroupName": "gpu",
   "MaxNodes": 10,
   "Region": "us-east-1",
   "SlurmSpecifications": {
      "CPUs": "32",
      "RealMemory": "128000",
      "Gres": "gpu:a100:4"
   },
   "PurchasingOption": "spot",
   "SpotOptions": {
      "AllocationStrategy": "price-capacity-optimized"
   },
   "LaunchTemplateSpecification": {
      "LaunchTemplateName": "gpu-template",
      "Version": "$Latest"
   },
   "LaunchTemplateOverrides": [
      {
         "InstanceType": "p4d.24xlarge"
      }
   ],
   "SubnetIds": ["subnet-12345678"]
}
```

### GPU Job Submission

Request GPUs in your job submission:

```bash
# Request 1 GPU
srun --gres=gpu:1 ./my_program

# Request 4 GPUs
sbatch --gres=gpu:4 script.sh

# Request specific GPU type
srun --gres=gpu:a100:2 ./training_job
```

### Important GPU Considerations

1. **Launch Template**: Must use a GPU-enabled AMI with NVIDIA drivers installed
2. **Instance Types**: Ensure the instance type in `LaunchTemplateOverrides` has the number of GPUs specified in `Gres`
3. **CUDA**: Pre-install CUDA toolkit and libraries in your AMI
4. **Pricing**: GPU instances are significantly more expensive; consider Spot instances
5. **Driver Installation**: Include driver installation in launch template user data if not pre-baked in AMI

### Example: Complete GPU Node Group

See [example-4-gpu-nodes.json](../examples/example-4-gpu-nodes.json) for a complete GPU configuration example.

## Examples

See the [examples directory](../examples/) for complete `partitions.json` examples:
- [example-1-ondemand-and-spot.json](../examples/example-1-ondemand-and-spot.json) - On-demand priority with spot overflow
- [example-2-multi-az.json](../examples/example-2-multi-az.json) - Multi-AZ with AZ-specific node groups
- [example-3-account-permissions.json](../examples/example-3-account-permissions.json) - Multiple partitions with account-based access control
- [example-4-gpu-nodes.json](../examples/example-4-gpu-nodes.json) - GPU instance configuration
