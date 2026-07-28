# Upgrade Guide - v2 to v3

This guide helps you migrate from plugin-v2 to plugin-v3.

## What's New in v3

### Documentation Improvements
- **Restructured documentation** - Moved from single README to organized docs/ directory
- **Comprehensive guides** - Security, networking, performance tuning, monitoring
- **Example configurations** - Ready-to-use examples for common scenarios
- **GPU/GRES documentation** - Complete guide for GPU workloads

### Behavior Changes (v3.1)

v3.1 adds three opt-in settings for tightly-coupled workloads. **All are off by default**, so
upgrading changes nothing until you enable them on a node group:

- `EnableMPISupport` - all-or-nothing launch: terminate a short or unhealthy allocation
  immediately instead of registering a partial one
- `PlacementGroupName` - set the placement group per node group instead of per launch template
- `MPIOptions` - timeout, readiness checks, and placement-group enforcement

To be explicit about what this is *not*: v2 already runs MPI jobs correctly, because Slurm
holds a job in `CONFIGURING` until every allocated node registers its slurmd. These settings
reduce the cost and opacity of *failed* launches. See
[MPI and Tightly-Coupled Workloads](mpi-support.md).

**If you enable `EnableMPISupport`, raise `ResumeTimeout`.** `resume.py` then blocks for up to
`MPIOptions.TimeoutSeconds` (default 300) while Slurm independently counts down
`ResumeTimeout`. Keep `ResumeTimeout >= TimeoutSeconds + 120`.

### No Breaking Changes

**Good news**: v3 is compatible with v2 configurations. Existing `config.json` and
`partitions.json` files work unchanged, and node groups without the v3.1 settings behave
exactly as they did on v2.

## Migration Path

### For New Deployments

Simply use the v3 branch:

```bash
cd /path/to/plugin
git checkout plugin-v3
```

No other changes needed.

### For Existing v2 Deployments

You have two options:

#### Option 1: Keep Running v2 (Recommended for Production)

If your current deployment works, **no action is required**.

- v2 will continue to function
- You can reference v3 documentation for troubleshooting
- Migrate when convenient during maintenance window

#### Option 2: Upgrade to v3

**Steps:**

1. **Backup current configuration:**

```bash
cp /path/to/plugin/config.json /path/to/plugin/config.json.backup
cp /path/to/plugin/partitions.json /path/to/plugin/partitions.json.backup
cp /etc/slurm/slurm.conf /etc/slurm/slurm.conf.backup
```

2. **Update plugin files:**

```bash
cd /path/to/plugin
git fetch origin
git checkout plugin-v3

# Or download directly
cd /path/to/plugin
for file in common.py resume.py suspend.py generate_conf.py change_state.py; do
  wget -O $file https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/$file
done
chmod +x *.py
```

3. **Verify configuration files are still valid:**

```bash
python3 -c "import json; json.load(open('config.json'))"
python3 -c "import json; json.load(open('partitions.json'))"
```

4. **Test plugin scripts:**

```bash
# Test config loading
python3 -c "import common; common.get_common('test')"

# Should not error
```

5. **No Slurm reconfiguration needed** - Your existing `slurm.conf` is compatible

6. **Monitor after upgrade:**

```bash
tail -f /var/log/slurm/aws_plugin.log
```

Watch for any errors on first few job launches.

## Configuration File Changes

### config.json

No changes required. Your v2 config.json works with v3.

**Optional additions available in v3:**

```json
{
  "SlurmConf": {
    "GresTypes": "gpu"
  }
}
```

Only add if you're configuring GPU nodes.

### partitions.json

No changes required. Your v2 partitions.json works with v3.

**New examples available:**
- GPU configurations
- Multi-AZ patterns
- Account-based access control

See [examples/](../examples/) directory.

## CloudFormation Template

The template.yaml file has minor updates but maintains backwards compatibility.

**Changes:**
- IMDSv2 get_nodename script improvements
- Documentation references updated
- No structural changes

**To update CloudFormation deployment:**

Not recommended for running stacks. Deploy new stack if needed.

## Documentation Migration

### Old README Structure

v2 had everything in one 607-line README.md

### New v3 Structure

```
docs/
├── onprem-to-aws-bursting.md  # Primary guide: on-prem to AWS
├── configuration.md        # config.json and partitions.json reference
├── manual-installation.md  # Step-by-step installation
├── cloudformation.md       # CFN deployment details
├── troubleshooting.md      # Common issues and solutions
├── security.md             # Security best practices
├── networking.md           # Network architecture
├── performance-tuning.md   # Optimization guide
├── monitoring.md           # CloudWatch and observability
├── advanced-usage.md       # Advanced scenarios
├── mpi-support.md          # Tightly-coupled workloads (v3.1)
├── upgrade-guide.md        # This file
└── testing.md              # Validation procedures

examples/
├── example-1-ondemand-and-spot.json
├── example-2-multi-az.json
├── example-3-account-permissions.json
├── example-4-gpu-nodes.json
├── example-5-mpi-workloads.json
├── config-basic.json
├── config-gpu.json
├── config-production.json
├── packer/                 # AMI builder
└── README.md
```

**Finding Information:**

| v2 Location | v3 Location |
|-------------|-------------|
| README "Concepts" | README "How It Works" |
| N/A | docs/onprem-to-aws-bursting.md (new) |
| N/A | docs/mpi-support.md (new in v3.1) |
| README "Plugin files" | docs/configuration.md |
| README "Manual deployment" | docs/manual-installation.md |
| README "CloudFormation" | docs/cloudformation.md |
| README "Partitions examples" | examples/*.json |
| N/A | docs/security.md (new) |
| N/A | docs/networking.md (new) |
| N/A | docs/performance-tuning.md (new) |
| N/A | docs/monitoring.md (new) |

## Testing the Upgrade

### Pre-Upgrade Checks

```bash
# 1. Verify current version works
srun -N1 hostname

# 2. Check current node states
sinfo

# 3. Note current configuration
cat /path/to/plugin/config.json
cat /path/to/plugin/partitions.json
```

### Post-Upgrade Tests

```bash
# 1. Test configuration loading
cd /path/to/plugin
python3 -c "import common; logger, config, partitions = common.get_common('test'); print('Config loaded successfully')"

# 2. Test single node launch
srun -N1 -p aws hostname

# 3. Check logs for errors
tail -50 /var/log/slurm/aws_plugin.log

# 4. Test multiple node launch
srun -N5 -p aws hostname

# 5. Verify nodes suspend correctly
# Wait for SuspendTime, then check:
sinfo  # Nodes should return to idle~ state
```

## Rollback Procedure

If issues occur after upgrade:

```bash
# 1. Stop any running jobs
scancel --state=PENDING --partition=aws

# 2. Restore v2 plugin files
cd /path/to/plugin
git checkout plugin-v2

# Or restore from backup
cp /path/to/plugin.v2.backup/*.py /path/to/plugin/

# 3. Restore configuration (if changed)
cp /path/to/plugin/config.json.backup /path/to/plugin/config.json
cp /path/to/plugin/partitions.json.backup /path/to/plugin/partitions.json

# 4. Restart slurmctld (optional, only if needed)
systemctl restart slurmctld

# 5. Test
srun -N1 -p aws hostname
```

## Common Questions

### Q: Do I need to update Slurm?

**A:** No. v3 is compatible with the same Slurm versions as v2 (20.02.3+).

### Q: Will running jobs be affected?

**A:** No. Plugin updates don't affect running jobs. New config only affects future launches.

### Q: Do I need to reconfigure Slurm?

**A:** No, unless you're adding new partitions or changing node counts.

### Q: Can I use v2 and v3 documentation interchangeably?

**A:** Yes. The plugin behavior is identical. v3 just has better organization and more detail.

### Q: Should I update the CloudFormation template?

**A:** Not for running stacks. Template changes are minimal. Only update for new deployments.

### Q: What if I encounter errors after upgrade?

**A:** Check the logs (`/var/log/slurm/aws_plugin.log`) and follow the [rollback procedure](#rollback-procedure) if needed.

## Getting Help

If you encounter issues:

1. Check [Troubleshooting Guide](troubleshooting.md)
2. Review plugin logs for errors
3. Compare your config with [examples](../examples/)
4. Open an issue on GitHub with:
   - Plugin version
   - Error messages from logs
   - Configuration files (redact sensitive info)
   - Steps to reproduce

## Migrating from v1 (2018 Version)

If you're still using the original 2018 plugin:

**Major changes from v1 to v2/v3:**
- Complete rewrite
- EC2 Fleet instead of RunInstances
- Decoupled node names from hostnames
- New configuration format
- Better error handling

**Migration path:**

Not directly supported. Recommended approach:

1. Deploy fresh v3 installation
2. Test thoroughly
3. Migrate workloads
4. Decommission v1

See [Manual Installation Guide](manual-installation.md) for fresh deployment.

## Version Compatibility Matrix

| Component | v2 | v3 | Notes |
|-----------|----|----|-------|
| Plugin code | ✓ | ✓ | v3.1 adds opt-in launch settings, off by default |
| config.json | ✓ | ✓ | v2 configs work in v3 |
| partitions.json | ✓ | ✓ | v2 configs work in v3 |
| slurm.conf | ✓ | ✓ | No changes needed |
| Python 3.6+ | ✓ | ✓ | Same requirement |
| boto3 | ✓ | ✓ | Same requirement |
| Slurm 20.02+ | ✓ | ✓ | Same requirement |
| Documentation | Old | New | Restructured in v3 |

## License Change

v3 introduces Apache 2.0 licensing:

- **v2**: MIT-0 (Amazon)
- **v3**: Apache 2.0 (maintains Amazon copyright, adds modifications copyright)

This does not affect your usage rights. Both are permissive open-source licenses.

## Next Steps

- Review [Configuration Reference](configuration.md)
- Explore [Examples](../examples/)
- Set up [Monitoring](monitoring.md)
- Apply [Security Best Practices](security.md)
- Optimize with [Performance Tuning](performance-tuning.md)
