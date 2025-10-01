# ⚠️ DEPRECATION NOTICE

**This branch (main/master) is deprecated and no longer actively maintained.**

This branch contains the original 2018 version of the AWS Plugin for Slurm.

## Active Development

Active development has moved to the **[plugin-v3](https://github.com/scttfrdmn/aws-plugin-for-slurm/tree/plugin-v3)** branch.

## Version History

- **Version 1 (2018)** - Original implementation (this branch)
- **Version 2 (2020)** - Complete rewrite with EC2 Fleet support ([plugin-v2](https://github.com/scttfrdmn/aws-plugin-for-slurm/tree/plugin-v2) - also deprecated)
- **Version 3 (2025)** - Documentation overhaul, enhanced security, GPU support ([plugin-v3](https://github.com/scttfrdmn/aws-plugin-for-slurm/tree/plugin-v3) - **active**)

## What's New in Version 3

Version 3 includes:

- **Complete documentation overhaul** - Restructured into modular guides
- **EC2 Fleet integration** - Spot instances and instance type flexibility
- **Enhanced security** - IMDSv2 support, comprehensive Munge configuration
- **GPU/GRES support** - Full documentation and examples
- **Decoupled node identity** - Node names independent of hostnames/IPs
- **Better error handling** - Robust handling of failed launches
- **Comprehensive guides**:
  - Security best practices
  - Network architecture
  - Performance tuning
  - CloudWatch monitoring
  - Advanced usage scenarios
  - Testing and validation procedures
- **Example configurations** - Ready-to-use templates for common scenarios
- **Apache 2.0 License** - Updated licensing

## Migration

**Do not attempt to upgrade from v1 to v3 in place.**

The plugin was completely rewritten between v1 and v2/v3. Recommended approach:

1. Deploy fresh v3 installation
2. Test thoroughly with your workloads
3. Migrate users and jobs
4. Decommission v1

See the [Manual Installation Guide](https://github.com/scttfrdmn/aws-plugin-for-slurm/blob/plugin-v3/docs/manual-installation.md) for deployment instructions.

## Using Version 1

If you need to continue using version 1:

1. This branch will remain available for reference
2. **No support or updates will be provided**
3. This version is not recommended for new deployments

## Documentation

For the latest documentation, visit the **[plugin-v3 branch](https://github.com/scttfrdmn/aws-plugin-for-slurm/tree/plugin-v3)**.

---

**Strong Recommendation**: Deploy v3 for new clusters. Version 1 is outdated and lacks modern AWS features, security enhancements, and comprehensive documentation.
