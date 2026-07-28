# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Apache 2.0 license
- This CHANGELOG file

### Changed
- License changed from MIT-0 to Apache 2.0
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
