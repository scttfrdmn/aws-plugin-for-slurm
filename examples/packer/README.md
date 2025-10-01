# Packer Template for Slurm Compute Node AMI

This Packer template automates building an AWS AMI for Slurm compute nodes that matches your on-premises cluster configuration.

## Why Use This?

Building an AMI manually is error-prone. This template ensures:
- Exact Slurm version match with your on-prem cluster
- Consistent configuration across all builds
- Reproducible builds - track changes in git
- Faster iteration - rebuild AMI in ~15 minutes

## Prerequisites

### 1. Install Packer

```bash
# macOS
brew install packer

# Linux
wget https://releases.hashicorp.com/packer/1.9.4/packer_1.9.4_linux_amd64.zip
unzip packer_1.9.4_linux_amd64.zip
sudo mv packer /usr/local/bin/

# Verify
packer version
```

### 2. AWS Credentials

```bash
# Configure AWS CLI
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_REGION="us-east-1"
```

### 3. Upload Munge Key to AWS Secrets Manager

```bash
# From your on-prem headnode:
aws secretsmanager create-secret \
  --name slurm/munge-key \
  --description "Munge authentication key for Slurm cluster" \
  --secret-binary fileb:///etc/munge/munge.key \
  --region us-east-1
```

## Quick Start

### 1. Copy and Customize Variables File

```bash
cd examples/packer
cp variables.pkrvars.hcl.example variables.pkrvars.hcl
```

Edit `variables.pkrvars.hcl`:

```hcl
slurm_version = "20.02.3"           # YOUR Slurm version
slurm_prefix  = "/nfs/slurm"        # YOUR Slurm install path
headnode_ip   = "10.0.1.100"        # YOUR headnode IP
vpc_id        = "vpc-xxxxxxxxxxxxx" # YOUR VPC
subnet_id     = "subnet-xxxxxxxxxx" # YOUR subnet
```

### 2. Validate Configuration

```bash
packer validate -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

### 3. Build AMI

```bash
packer build -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

**Build time**: ~15-20 minutes

**Output**: AMI ID will be displayed at the end and saved to `manifest.json`

### 4. Use the AMI

Update your launch template to use the new AMI:

```bash
AMI_ID=$(jq -r '.builds[0].artifact_id' manifest.json | cut -d: -f2)

aws ec2 create-launch-template-version \
  --launch-template-id lt-xxxxx \
  --source-version 1 \
  --launch-template-data "{\"ImageId\":\"$AMI_ID\"}" \
  --version-description "Updated with Packer-built AMI"
```

## What Gets Installed

The Packer template:

1. **Starts with**: Latest Amazon Linux 2 AMI
2. **Installs Slurm**: Exact version you specify, compiled with same prefix
3. **Configures Munge**: Prepared to receive key from Secrets Manager at boot
4. **Sets up NFS client**: Ready to mount from your headnode
5. **Creates systemd services**: slurmd, munge, with proper dependencies
6. **Enables IMDSv2**: For secure instance metadata access
7. **Adds init script**: `/usr/local/bin/slurm-init.sh` runs on first boot

## Customization

### Use Different Base OS

For Rocky Linux 8:

```hcl
base_ami_owner       = "679593333241"  # Rocky Linux project
base_ami_name_filter = "Rocky-8-EC2-Base-*-x86_64"
```

For Ubuntu 22.04:

```hcl
base_ami_owner       = "099720109477"  # Canonical
base_ami_name_filter = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
```

### Add Custom Software

Edit `slurm-compute-node.pkr.hcl` and add a provisioner:

```hcl
provisioner "shell" {
  inline = [
    "echo '==> Installing custom software'",
    "sudo yum install -y your-package",
    "sudo pip3 install your-python-package"
  ]
}
```

### Add GPU Support

For GPU instances, add NVIDIA drivers:

```hcl
provisioner "shell" {
  inline = [
    "echo '==> Installing NVIDIA drivers'",
    "sudo yum install -y gcc kernel-devel-$(uname -r)",
    "wget https://us.download.nvidia.com/tesla/525.116.04/NVIDIA-Linux-x86_64-525.116.04.run",
    "sudo sh NVIDIA-Linux-x86_64-525.116.04.run --silent",
    "nvidia-smi"
  ]
}
```

## Troubleshooting

### Build Fails: "Cannot connect to instance"

**Cause**: Subnet has no internet access

**Solution**: Use a public subnet or private subnet with NAT gateway

```bash
# Verify subnet has internet
aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=subnet-xxxxx" \
  --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`]'
```

### Build Fails: Slurm download fails

**Cause**: Wrong Slurm version or download URL changed

**Solution**: Check available versions at https://download.schedmd.com/slurm/

```bash
curl -s https://download.schedmd.com/slurm/ | grep -o 'slurm-[0-9.]*.tar.bz2' | sort -V
```

### AMI boots but slurmd doesn't start

**Cause**: NFS mount failed or Munge key retrieval failed

**Solution**: Check instance system log:

```bash
aws ec2 get-console-output --instance-id i-xxxxx --output text
```

Look for errors in the `/usr/local/bin/slurm-init.sh` script output.

## Advanced Usage

### Build for Multiple Slurm Versions

```bash
# Build for v20.02.3
packer build -var 'slurm_version=20.02.3' -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl

# Build for v21.08.8
packer build -var 'slurm_version=21.08.8' -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

### Automated AMI Updates

Add to CI/CD pipeline:

```yaml
# GitHub Actions example
name: Build Slurm AMI
on:
  push:
    paths:
      - 'examples/packer/**'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Packer
        uses: hashicorp/setup-packer@v2
      - name: Build AMI
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        run: |
          cd examples/packer
          packer build -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

### Share AMI Across Accounts

```bash
# Get AMI ID from manifest
AMI_ID=$(jq -r '.builds[0].artifact_id' manifest.json | cut -d: -f2)

# Share with another AWS account
aws ec2 modify-image-attribute \
  --image-id $AMI_ID \
  --launch-permission "Add=[{UserId=123456789012}]"
```

## Maintenance

### Rebuild AMI Monthly

Security patches and updates:

```bash
# Setup cron job
0 2 1 * * cd /path/to/packer && packer build -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

### Track AMI Versions

Tag AMIs with build date:

```hcl
tags = {
  BuildDate = "{{timestamp}}"
  SlurmVersion = var.slurm_version
  Purpose = "Production"
}
```

### Deregister Old AMIs

```bash
# List all your Slurm AMIs
aws ec2 describe-images \
  --owners self \
  --filters "Name=name,Values=slurm-compute-*" \
  --query 'Images[*].[ImageId,CreationDate,Name]' \
  --output table

# Deregister old AMI
aws ec2 deregister-image --image-id ami-xxxxx
```

## Files

- `slurm-compute-node.pkr.hcl` - Main Packer template
- `variables.pkrvars.hcl.example` - Example variables file
- `manifest.json` - Build output (generated after build)

## See Also

- [On-Premises to AWS Bursting Guide](../../docs/onprem-to-aws-bursting.md)
- [Packer Documentation](https://www.packer.io/docs)
- [AWS AMI Best Practices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AMIs.html)
