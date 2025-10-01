# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/aws-samples/aws-plugin-for-slurm/compare/plugin-v3...HEAD
[3.0.0]: https://github.com/aws-samples/aws-plugin-for-slurm/compare/plugin-v2...plugin-v3
[2.0.0]: https://github.com/aws-samples/aws-plugin-for-slurm/compare/master...plugin-v2
[1.0.0]: https://github.com/aws-samples/aws-plugin-for-slurm/releases/tag/v1.0.0
