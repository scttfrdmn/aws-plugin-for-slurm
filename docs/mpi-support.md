# MPI and Tightly-Coupled Workloads

**Added in:** v3.1.0

## Start here: v2 already runs MPI jobs

If you are on plugin v2 and want to run MPI, **you do not need this page to get a working
job.** v2 runs MPI correctly, and it always has.

The reason is Slurm, not the plugin. When a job is allocated cloud nodes that are powered
down, slurmctld puts the job in `CONFIGURING` (`alloc#`) and **will not start the job step
until every node in the allocation has registered its slurmd.** That is what `ResumeTimeout`
is the deadline for. Your batch script does not begin running until the allocation is
complete, so `mpirun` never sees a half-built node list.

Placement groups and EFA work on v2 too. Both are launch-template settings, and v2 passes
your launch template to EC2 Fleet unchanged — put `Placement.GroupName` in the template and
v2 will honor it. See [Advanced Usage](advanced-usage.md#placement-groups).

So what does v3 add? Three things, none of which is "makes MPI work":

| v3 addition | What it changes vs v2 |
|---|---|
| **All-or-nothing launch** | On a short or failed launch, v3 terminates the partial allocation in seconds instead of leaving it to idle until `ResumeTimeout` expires. Saves money and surfaces the error. |
| **Placement group in `partitions.json`** | Set the placement group per node group instead of per launch template. One template can now serve several node groups. |
| **Readiness checks and logging** | The plugin log names the node and the failed check, instead of leaving you to infer a boot failure from an opaque `DOWN` transition. |

These are cost, ergonomics, and diagnosability improvements. If v2 MPI works for you and you
are not hitting failed launches at scale, there is little reason to change.

---

## When v3's additions are worth it

Adopt the settings on this page if you recognize these:

- **Large allocations that sometimes come up short.** Requesting 32 nodes and getting 27 is
  common in a constrained placement group. On v2 the 27 boot, sit idle, and the job dies
  when `ResumeTimeout` expires on the missing 5 — you pay for all 27 the whole time. v3
  detects the shortfall immediately and terminates.
- **Expensive instances.** At c7gn/c6in on-demand rates, minutes of idle nodes per failed
  launch adds up.
- **Opaque boot failures.** A node that boots but never starts slurmd looks identical to a
  slow node until `ResumeTimeout`. The readiness check names it.

---

## Prerequisites

### Placement group

A cluster placement group is what gets you low inter-node latency. Create one:

```bash
aws ec2 create-placement-group \
  --group-name slurm-mpi-pg \
  --strategy cluster \
  --region us-east-1
```

A cluster placement group **cannot span availability zones**, so an MPI node group must use
a single subnet:

```json
"SubnetIds": ["subnet-11111111"]
```

The plugin logs a warning if you configure a placement group with more than one subnet.

### Matching MPI on the AMI

Every node needs the same MPI library at the same version, and slurmd must start on boot.
Use the [Packer template](../examples/packer/) to keep AMIs consistent.

### On-demand, not spot

A spot interruption anywhere in the allocation kills the whole job, and MPI jobs rarely
checkpoint often enough to make that cheap:

```json
"PurchasingOption": "on-demand"
```

---

## Configuration

Add to a node group in `partitions.json`:

```json
{
  "NodeGroupName": "compute",
  "MaxNodes": 32,
  "Region": "us-east-1",

  "EnableMPISupport": true,
  "PlacementGroupName": "slurm-mpi-pg",

  "MPIOptions": {
    "TimeoutSeconds": 300,
    "HealthChecks": ["slurmd"],
    "RequirePlacementGroup": true
  },

  "SlurmSpecifications": {"CPUs": "64", "RealMemory": "120000"},
  "PurchasingOption": "on-demand",
  "LaunchTemplateSpecification": {"LaunchTemplateName": "mpi-template", "Version": "$Latest"},
  "LaunchTemplateOverrides": [{"InstanceType": "c7gn.16xlarge"}],
  "SubnetIds": ["subnet-11111111"]
}
```

### Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `EnableMPISupport` | boolean | `false` | Enable all-or-nothing launch for this node group |
| `PlacementGroupName` | string | none | Placement group for the fleet. Overrides `Placement` in the launch template |
| `MPIOptions.WaitForAllNodes` | boolean | `true` | Set `false` to keep the other settings but launch asynchronously like v2 |
| `MPIOptions.TimeoutSeconds` | integer | `300` | How long to wait for the full allocation before terminating it |
| `MPIOptions.HealthChecks` | array | `["slurmd"]` | Readiness checks. `[]` disables them |
| `MPIOptions.RequirePlacementGroup` | boolean | `false` | Refuse to launch if `PlacementGroupName` is unset |

**Readiness checks:**

- `slurmd` — TCP connect to port 6818. This is the default.
- `network` — ICMP ping. **Opt-in, and off by default on purpose:** if your security group
  does not allow ICMP, every node fails this check, and at the timeout the plugin terminates
  a perfectly healthy allocation. Only enable it if ICMP is permitted from the headnode.

Setting `EnableMPISupport` on a node group has no effect on other node groups. Untouched
node groups keep exact v2 behavior.

### `TimeoutSeconds` must be less than `ResumeTimeout`

This is the one setting that will bite you. `resume.py` now **blocks** for up to
`TimeoutSeconds` while waiting for the allocation. Slurm is independently counting down
`ResumeTimeout` (in `config.json`) the whole time. If `TimeoutSeconds` >= `ResumeTimeout`,
Slurm gives up and marks your nodes `DOWN` while the plugin is still waiting — you get the
worst of both designs.

Leave real headroom:

```
MPIOptions.TimeoutSeconds  <=  ResumeTimeout - 120
```

The plugin warns at startup when a node group leaves too little headroom:

```
WARNING - Node group mpi/compute: MPIOptions.TimeoutSeconds (300) leaves too little
          headroom under ResumeTimeout (300). Slurm may mark nodes DOWN while resume.py
          is still waiting. Set ResumeTimeout to at least 420.
```

This is a warning, not an error — `config.json` is not necessarily the authoritative
`slurm.conf`, so the plugin will not refuse to launch over it. The shipped CloudFormation
template sets `ResumeTimeout: 600`, which accommodates the default `TimeoutSeconds: 300`.
See [Performance Tuning](performance-tuning.md#resumetimeout).

### `ResumeRate` must not split the allocation

Slurm launches at most `ResumeRate` nodes per minute, calling `ResumeProgram` once per
batch. Each invocation only knows about its own subset, so a 40-node job under
`ResumeRate: 20` becomes two independent 20-node waits — the all-or-nothing guarantee
applies to each half, not the job. Keep `ResumeRate` at or above your largest MPI
allocation.

---

## Slurm configuration

```bash
PartitionName=mpi Nodes=mpi-compute-[0-31] Default=NO OverSubscribe=NO State=UP
NodeName=mpi-compute-[0-31] State=CLOUD CPUs=64 Feature=lowlatency
```

`OverSubscribe=NO` keeps other jobs off nodes in an MPI allocation.

---

## Usage

Submitting a job is unchanged. Your MPI application needs no modification.

```bash
srun -p mpi -N 4 -n 256 ./mpi_application
```

```bash
#!/bin/bash
#SBATCH --partition=mpi
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=64
#SBATCH --time=02:00:00

srun ./cfd_solver input.dat
```

### What the log shows

```
tail -f /var/log/slurm/aws_plugin.log
```

A successful launch:

```
INFO - Sync launch enabled: launching 4 nodes as an all-or-nothing group
INFO - Sync launch: waiting for 4 instances to be ready (timeout=300s, checks=slurmd)
INFO - Sync launch: instance i-0abc1 ready (10.1.1.50) [1/4]
INFO - Sync launch: instance i-0abc2 ready (10.1.1.51) [2/4]
INFO - Sync launch: instance i-0abc3 ready (10.1.1.52) [3/4]
INFO - Sync launch: instance i-0abc4 ready (10.1.1.53) [4/4]
INFO - Sync launch: all 4 instances ready after 42.8s
INFO - Sync launch: all 4 nodes configured and ready
```

A short launch, cleaned up instead of left idle:

```
ERROR - Sync launch failed: EC2 Fleet returned 11 of 12 requested instances.
        A partial allocation cannot satisfy a tightly-coupled job.
WARNING - Terminating 11 instance(s): i-0abc1, i-0abc2, ...
WARNING - EC2 Fleet error codes: InsufficientFreeAddressesInSubnet
```

A node that dies while the plugin is waiting — detected immediately rather than at the timeout:

```
ERROR - Sync launch failed: Instance i-0abc2 entered state "shutting-down" while waiting for readiness
WARNING - Terminating 4 instance(s): i-0abc1, i-0abc2, i-0abc3, i-0abc4
```

---

## Measured behavior

Measured on real EC2 (us-west-2, `c6g.large`, on-demand, cluster placement group, single
subnet, `HealthChecks: ["slurmd"]`). "Time to all ready" is `CreateFleet` to the last node
passing its readiness check:

| Nodes | Time to all ready |
|---|---|
| 2 | 44.7s |
| 4 | 42.8s |
| 8 | 49.2s |
| 16 | 44.9s |

Launch time is dominated by instance boot, not allocation size — waiting for the whole
allocation costs little over waiting for one node. Your numbers will be higher with a
bootstrap-heavy AMI; measure your own before setting `TimeoutSeconds`.

Failure paths, same environment:

| Scenario | v3 | Pre-fix behavior |
|---|---|---|
| Fleet returns 11 of 12 (subnet out of IPs) | Detected and all 11 terminated in **3.9s** | Logged "all 11 instances ready", registered 11 nodes, left them running until `ResumeTimeout` killed the job |
| One node terminated mid-wait | Failed fast in **8s**, all 4 terminated | Still polling at 3/4 after 187s; would have waited the full `TimeoutSeconds` |
| ICMP blocked by security group, `HealthChecks: ["network"]` | Healthy allocation terminated at the timeout — the reason `network` is not a default | Same, but `network` *was* a default, so this hit every ICMP-restricted VPC |

In the ICMP case both nodes were fully booted and accepting TCP; only ping was blocked.

---

## Verification

Confirm the placement group actually took effect:

```bash
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
  --query 'Reservations[].Instances[].[InstanceId,Placement.GroupName,Placement.AvailabilityZone]' \
  --output table
```

Measure latency with OSU Micro-Benchmarks. In a cluster placement group expect single-digit
microseconds; without one, tens to hundreds:

```bash
srun -p mpi -N 2 -n 2 /usr/local/libexec/osu-micro-benchmarks/mpi/pt2pt/osu_latency
```

Check a specific node by hand:

```bash
python3 health_check.py 10.1.1.50 --checks slurmd
```

---

## Troubleshooting

### Allocation terminated: "returned N of M requested instances"

Not enough capacity for the full allocation. The plugin gave the partial capacity back
rather than let it idle.

- Add instance type flexibility to `LaunchTemplateOverrides` — the most effective fix
- Request fewer nodes; cluster placement groups often cap out around 20–60 instances
  depending on type
- Try another AZ or region

### Allocation terminated: "Only N/M instances ready"

Instances launched but did not become ready in time.

- If `network` is in `HealthChecks`, **remove it first** and retry. Blocked ICMP is the most
  common cause of this exact message.
- Confirm slurmd starts on boot in the AMI and that the security group allows 6818 from the
  headnode
- Raise `TimeoutSeconds` for slow-booting AMIs — and raise `ResumeTimeout` to match

### Nodes marked DOWN while the plugin is still waiting

`TimeoutSeconds` >= `ResumeTimeout`. See
[TimeoutSeconds must be less than ResumeTimeout](#timeoutseconds-must-be-less-than-resumetimeout).

### Node group is skipped with no instances launched

`MPIOptions.RequirePlacementGroup` is `true` but `PlacementGroupName` is unset. Set the
group, or drop the requirement.

### High latency between nodes

Verify the placement group applied (command above). A `GroupName` of `null` means the
setting did not reach EC2. Check that `PlacementGroupName` is on the node group and that the
node group uses a single subnet.

### Job fails at MPI_Init

Nodes are in Slurm but cannot talk to each other. Allow all traffic within the compute
security group:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-compute --source-group sg-compute --protocol all
```

Then confirm one MPI version across nodes: `srun -p mpi -N 4 mpirun --version`.

---

## Performance notes

**Instance choice.** Network bandwidth matters more than core count for most MPI codes.
Prefer current-generation network-optimized types — `c7gn` (Graviton), `c7i`, `c6in` all
reach 200 Gbps. Add EFA in the launch template for latency-sensitive codes; the plugin does
not configure EFA for you. See [Advanced Usage](advanced-usage.md#efa-elastic-fabric-adapter).

**Shared filesystem.** NFS from on-prem over VPN is the usual bottleneck for MPI-IO, at tens
of milliseconds. FSx for Lustre or local NVMe scratch are far better; EFS sits in between.

**Process pinning.** `srun --cpu-bind=cores`, or `I_MPI_PIN=1` for Intel MPI.

---

## Limitations

- One placement group per node group. Define multiple node groups if you need more.
- Single AZ per MPI node group, inherent to cluster placement groups. No AZ failover.
- Concurrent MPI jobs compete for the same placement group capacity.
- EFA is not auto-configured; set it in the launch template.
- The plugin cannot re-request capacity after a short launch. Slurm requeues the job
  according to your partition settings.

---

## FAQ

**Does this make MPI work where v2 could not?**
No. v2 runs MPI correctly — Slurm holds the job until all nodes register. v3 makes failed
launches cheaper and easier to diagnose.

**Can I mix MPI and non-MPI partitions?**
Yes. The settings are per node group; everything else behaves exactly as on v2.

**What happens when one node fails its readiness check?**
The whole allocation is terminated so you are not billed for unusable nodes. Slurm requeues
the job per your partition settings.

**Do I need to change my MPI application?**
No.

**Can I get the placement group convenience without the blocking wait?**
Yes — set `PlacementGroupName` and `MPIOptions.WaitForAllNodes: false`.

---

## See also

- [Configuration Reference](configuration.md) — full `partitions.json` schema
- [Performance Tuning](performance-tuning.md) — `ResumeTimeout` and `ResumeRate`
- [Advanced Usage](advanced-usage.md) — placement groups, EFA, FSx for Lustre
- [Upgrade Guide](upgrade-guide.md) — v2 to v3
- [Example: MPI workloads](../examples/example-5-mpi-workloads.json)
