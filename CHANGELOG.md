# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — testing and CI (v3.3)

- Automated test suite (`tests/`, 160 tests, standard library only). Requires no AWS
  account, no credentials and no Slurm controller: `boto3` is shadowed by a fake that
  journals every EC2 call, `scontrol`/`sinfo` are stubbed on `SlurmBinPath`, and the plugin
  scripts run as subprocesses the way `slurmctld` invokes them. Assertions read the call
  journal and the `scontrol` argv log rather than log text. Runs in ~15 seconds.
- Mutation tests (`tests/test_mutations.py`, opt-in via `RUN_MUTATION_TESTS=1`). Each fixed
  bug is reintroduced into a copy of the plugin and the tests that name it must fail — a
  regression test that passes against the broken code pins nothing. All 11 mutations are
  caught.
- `tests/test_python_floor.py` enforces the documented Python 3.6 floor by static scan,
  covering both the plugin and the suite itself. CI cannot install 3.6 on current GitHub
  runners, so the floor is checked rather than exercised.
- `tests/test_docs_links.py` checks every internal documentation link and heading anchor.
  External URLs are deliberately not checked, so CI does not fail when an unrelated site
  is down.
- GitHub Actions CI (`.github/workflows/ci.yml`) on every push and pull request:
  byte-compile all plugin scripts across Python 3.7–3.13, parse and schema-validate every
  `examples/*.json`, run the suite on 3.7/3.9/3.12/3.13, run the mutation tests, and check
  documentation links and the version floor.

### Decided — no `nfs` readiness check (#10)

The `nfs` health check will not come back. The headnode cannot observe a compute node's
mount table, and every mechanism that would let it (SSH from the headnode, an agent) adds a
key-management or daemon dependency to answer a question the node already knows. The v3.1
stub that validated and then always returned success was removed in `47dde28`; a check that
always passes is worse than none.

The documented answer is to gate `slurmd` on the mount in the node's own boot, so the
existing `slurmd` check covers it and the plugin needs to know nothing about the filesystem.
`docs/mpi-support.md` gives both a `RequiresMountsFor=` drop-in and a boot-script guard.

While writing this up, the shipped `examples/packer/` template turned out to gate `slurmd`
on the mount only by accident: its `After=…remote-fs.target nfs.target` cannot order against
a mount with no `/etc/fstab` entry at AMI build time, since `slurm-init.sh` writes that entry
at boot. What actually held was that `slurmd` requires Munge, whose key the same script
fetches after mounting. Baking the Munge key into the AMI — a reasonable optimization — would
have removed the only ordering and let `slurmd` start with no shared filesystem. Now explicit:

- `examples/packer/` gained a `mountpoint -q` `ExecStartPre` on `slurmd.service` and a
  `mountpoint` check after `mount` in the boot script.
- The walkthrough script in `docs/onprem-to-aws-bursting.md` used `mount -a`, which returns
  0 even when an individual entry fails, and then started `slurmd` regardless. It now
  verifies the mountpoint and exits instead.

### Fixed

- `resume.py` and `health_check.py` called `subprocess.run(capture_output=True)`, which is
  Python 3.7+, while README and the upgrade guide both promise 3.6+. On a RHEL/CentOS 7
  headnode running system Python 3.6 the `ping` call raised `TypeError`, which the
  surrounding `except Exception` swallowed into "unreachable". In `resume.py` this affects
  node groups that opt into `HealthChecks: ["network"]` — a healthy allocation would be
  reported unhealthy and, under `EnableMPISupport`, terminated. `["slurmd"]` is the default
  and uses a socket, so it was unaffected. In `health_check.py` the CLI checks `network` by
  default, so `health_check.py <ip>` reported a hard FAIL on every reachable node.
  Replaced with `stdout=`/`stderr=PIPE`; the floor is now enforced by a test.
- Three internal documentation links in `docs/onprem-to-aws-bursting.md` pointed at
  headings that had been renamed or that live in a different file (`advanced-usage.md#gpu-support`,
  `configuration.md#spot-instances`, `advanced-usage.md#multi-region`).
- `suspend.py` aborted the entire suspend run with `NameError` when `describe_instances`
  failed for one node group, falling through to an unassigned `response_describe`. Every
  remaining instance stayed running and billing. It now logs the node group and continues.
- `suspend.py` did not reset `node_name` between instances, so an instance with no `Name`
  tag was logged under the previously-seen node's name — misleading exactly when an
  operator is chasing a leaked instance.

### Changed

- License reverted to **MIT-0** (MIT No Attribution) to match the upstream AWS plugin.
  v3.0.0 had relicensed to Apache 2.0. Amazon's original license text is restored verbatim,
  with the fork's modifications copyright retained. MIT-0 is the more permissive of the two
  — it requires no attribution — so this does not restrict any existing use.

### Fixed — correctness and upstream parity (v3.2)

- `change_state.py` power-save rules never fired. Slurm emits compound states like
  `DOWN+CLOUD+POWERED_DOWN`, and the code tested list membership for a bare `'POWER'`,
  which never matches `POWERED_DOWN`/`POWERING_UP`/`POWER_DOWN`. A node that hit
  `ResumeTimeout` was therefore sent `POWER_DOWN` again instead of being reset to `IDLE`,
  leaving it permanently unavailable, and the `DRAIN` + power-save `UNDRAIN` rule never ran
  at all. Carried from upstream PR #36.
- `common.py:update_node()` ignored the `scontrol` exit code, so a failed
  `scontrol update` was logged as success. `change_state.py` reported state changes that
  never happened and `resume.py` reported nodes as configured when they were never
  registered. `run_scommand()` gained an opt-in `check` argument; read-only callers keep
  their tolerant behavior. Carried from upstream PR #36.
- Synchronous launch now tears down the allocation when a node cannot be registered in
  Slurm. Previously it logged "all N nodes configured and ready" even if every
  `scontrol update` failed — the same false success that all-or-nothing launch exists to
  prevent. Exposed by the `update_node` fix above.
- `resume.py` asynchronous path assigned the *previous* instance's IP address to a node
  when `describe_instances` returned no match for it, registering two Slurm nodes against
  one instance; on the first iteration it raised `NameError` and aborted the whole resume.
  Both now log a per-node error and skip.
- `common.py:get_node_state()` removed. It referenced two undefined variables
  (`scontrol_path`, `arguments`) and had no callers.
- `generate_conf.py` raised `NameError: name 'false' is not defined` on a malformed `Gres`
  specification, via `assert false` plus an incomplete format string. It now raises an
  error naming the offending value and node group.
- Warn when an MPI-enabled partition sets `OverSubscribe` to a value that permits sharing
  (`YES`/`FORCE`). `NO` and `EXCLUSIVE` are both accepted.
- Fork-specific URLs in `CHANGELOG.md`, `CONTRIBUTING.md`, `docs/cloudformation.md`, and
  `docs/upgrade-guide.md` pointed at `aws-samples`, where the referenced `plugin-v3` branch
  does not exist.

### Added — tightly-coupled workload support (v3.1)

Three opt-in `partitions.json` settings, all off by default. Node groups that do not set them
behave exactly as on v2.

- `EnableMPISupport` — all-or-nothing launch. `resume.py` waits for the full allocation and
  terminates it if any node is missing or unhealthy, instead of registering a partial
  allocation that Slurm will kill at `ResumeTimeout`.
- `PlacementGroupName` — placement group per node group rather than per launch template.
- `MPIOptions` — `WaitForAllNodes`, `TimeoutSeconds`, `HealthChecks`, `RequirePlacementGroup`.
- `health_check.py` — standalone readiness checker for a node IP.
- Startup warnings for `config.json`/`partitions.json` combinations that are individually
  valid but conflict at runtime: `MPIOptions.TimeoutSeconds` without enough headroom under
  `ResumeTimeout`, and `ResumeRate` below a sync-launch node group's `MaxNodes`. Warnings
  only — the plugin never refuses to launch over them.
- `docs/mpi-support.md` — guide, explicit that v2 already runs MPI correctly and that these
  settings address the cost and diagnosability of *failed* launches.
- `.gitignore` — Python bytecode, local `config.json`/`partitions.json`, plugin log.

### Fixed

- All-or-nothing launch no longer accepts a partial EC2 Fleet result. Previously, requesting
  16 nodes and receiving 12 logged "all 12 nodes configured and ready" and left the job to
  die at `ResumeTimeout` — the exact failure the feature exists to prevent.
- Readiness waiting now fails fast when an instance enters a terminal state, instead of
  waiting out the full timeout for a node that will never appear.
- `MPIOptions.WaitForAllNodes` and `MPIOptions.RequirePlacementGroup` are now honored. Both
  were validated but never read.
- `network` (ICMP) removed from the default health checks. Security groups commonly block
  ICMP, and a blocked ping caused every node to fail and a healthy allocation to be
  terminated. Opt in explicitly.
- `nfs` health check removed. It was accepted by validation and always returned success,
  giving false confidence.
- Sockets in port checks are now closed on the success path.
- Warn when a placement group is configured with multiple subnets, which a cluster placement
  group cannot span.
- `template.yaml` raised `ResumeTimeout` from 300 to 600 (and `SuspendTime` 350 to 650). The
  old value equaled the default `MPIOptions.TimeoutSeconds`, so a CloudFormation-deployed
  cluster that enabled `EnableMPISupport` had Slurm marking nodes `DOWN` while `resume.py`
  was still waiting for them.

### Removed

- `docs/IMPLEMENTATION_PLAN_MPI.md` — internal planning document. Its problem statement
  claimed MPI jobs start before all nodes are ready, which contradicts Slurm's
  `CONFIGURING`-state behavior and this project's own bursting and troubleshooting guides.

### Added
- Comprehensive documentation restructure with separate guides for configuration, installation, troubleshooting, and advanced topics
- GPU/GRES support documentation
- CloudFormation deployment guide
- Security best practices guide
- Network architecture documentation
- Performance tuning guide
- Monitoring and observability guide
- Advanced usage scenarios
- Testing and validation procedures
- Upgrade guide from v2 to v3
- Example configurations for common use cases
- Apache 2.0 license *(later reverted to MIT-0 — see Unreleased)*
- This CHANGELOG file

### Changed
- License changed from MIT-0 to Apache 2.0 *(later reverted — see Unreleased)*
- Documentation split into modular files for better organization
- README streamlined with quick start and better navigation
- Configuration examples moved to dedicated examples directory

## [3.0.0] - 2025-09-30

### Added
- Complete documentation overhaul with modular structure
- Separate configuration reference guide
- Detailed manual installation guide
- Comprehensive troubleshooting guide
- Example configurations for multiple scenarios
- IMDSv2 support for instance metadata
- GPU/GRES configuration support
- Enhanced error handling and logging

### Changed
- Restructured project documentation from single README to organized docs/ directory
- Updated branch references from plugin-v2 to plugin-v3
- Improved example configurations with detailed explanations

### Breaking Changes
- Documentation location changes (content moved from README to docs/)
- Branch naming convention (plugin-v3 is now the active development branch)

## [2.0.0] - Original AWS Release

### Added
- EC2 Fleet integration for Spot instances and instance type flexibility
- Decoupled node identity from EC2 instance attributes
- Support for multiple regions and AWS profiles
- Dynamic IP address and hostname management
- Tag-based instance identification
- Automatic node state management via cron job
- Configuration generation tool (generate_conf.py)
- Support for both Spot and On-Demand instances
- Instance type diversification
- Multi-AZ subnet support

### Changed
- Complete rewrite of original 2018 plugin
- Improved error handling for failed launches
- Better node state transitions

### Removed
- Static IP address requirements
- Direct hostname binding

## [1.0.0] - 2018 - Original Release

Initial release by AWS of the Slurm cloud bursting plugin.

[Unreleased]: https://github.com/scttfrdmn/aws-plugin-for-slurm/compare/plugin-v3...HEAD
[3.0.0]: https://github.com/scttfrdmn/aws-plugin-for-slurm/compare/plugin-v2...plugin-v3
[2.0.0]: https://github.com/aws-samples/aws-plugin-for-slurm/compare/master...plugin-v2
[1.0.0]: https://github.com/aws-samples/aws-plugin-for-slurm/releases/tag/v1.0.0
