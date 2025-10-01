# Packer template for building Slurm compute node AMI
# This creates an AMI with Slurm installed at a specific version
# for cloud bursting from on-premises clusters

packer {
  required_plugins {
    amazon = {
      version = ">= 1.0.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

# Variables - customize these for your environment
variable "slurm_version" {
  type        = string
  description = "Slurm version to install (must match on-prem cluster)"
  default     = "20.02.3"
}

variable "slurm_prefix" {
  type        = string
  description = "Installation prefix for Slurm (must match on-prem)"
  default     = "/nfs/slurm"
}

variable "aws_region" {
  type        = string
  description = "AWS region to build AMI in"
  default     = "us-east-1"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID to build in (must have internet access)"
}

variable "subnet_id" {
  type        = string
  description = "Subnet ID to build in (must have internet access)"
}

variable "headnode_ip" {
  type        = string
  description = "On-premises headnode IP address for NFS mount testing"
}

variable "nfs_export" {
  type        = string
  description = "NFS export path on headnode"
  default     = "/nfs"
}

variable "munge_key_s3" {
  type        = string
  description = "S3 URI for Munge key (e.g., s3://bucket/path/munge.key)"
  default     = ""
}

variable "munge_key_secretsmanager" {
  type        = string
  description = "AWS Secrets Manager secret name for Munge key"
  default     = "slurm/munge-key"
}

variable "base_ami_owner" {
  type        = string
  description = "AMI owner for base image"
  default     = "amazon"
}

variable "base_ami_name_filter" {
  type        = string
  description = "Filter for base AMI name"
  default     = "amzn2-ami-hvm-*-x86_64-gp2"
}

# Data source to find latest base AMI
data "amazon-ami" "base" {
  filters = {
    name                = var.base_ami_name_filter
    root-device-type    = "ebs"
    virtualization-type = "hvm"
    state               = "available"
  }
  most_recent = true
  owners      = [var.base_ami_owner]
  region      = var.aws_region
}

# AMI builder configuration
source "amazon-ebs" "slurm_compute" {
  ami_name        = "slurm-compute-${var.slurm_version}-{{timestamp}}"
  ami_description = "Slurm ${var.slurm_version} compute node for cloud bursting from on-premises"
  instance_type   = "t3.medium"
  region          = var.aws_region
  vpc_id          = var.vpc_id
  subnet_id       = var.subnet_id
  source_ami      = data.amazon-ami.base.id
  ssh_username    = "ec2-user"

  # Enable IMDSv2
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  # Tags for the AMI
  tags = {
    Name             = "slurm-compute-${var.slurm_version}"
    SlurmVersion     = var.slurm_version
    OS               = "Amazon Linux 2"
    ManagedBy        = "Packer"
    Purpose          = "CloudBursting"
    BuiltAt          = "{{timestamp}}"
  }

  # Snapshot tags
  snapshot_tags = {
    Name         = "slurm-compute-${var.slurm_version}-snapshot"
    SlurmVersion = var.slurm_version
  }

  # Launch configuration
  launch_block_device_mappings {
    device_name = "/dev/xvda"
    volume_size = 20
    volume_type = "gp3"
    delete_on_termination = true
  }
}

# Build steps
build {
  sources = ["source.amazon-ebs.slurm_compute"]

  # Update system packages
  provisioner "shell" {
    inline = [
      "echo '==> Updating system packages'",
      "sudo yum update -y",
      "sudo yum install -y wget curl jq"
    ]
  }

  # Install Slurm dependencies
  provisioner "shell" {
    inline = [
      "echo '==> Installing Slurm build dependencies'",
      "sudo yum install -y \\",
      "  munge munge-libs munge-devel \\",
      "  openssl openssl-devel \\",
      "  pam-devel \\",
      "  numactl numactl-devel \\",
      "  hwloc hwloc-devel \\",
      "  lua lua-devel \\",
      "  readline-devel \\",
      "  rrdtool-devel \\",
      "  ncurses-devel \\",
      "  man2html \\",
      "  libibmad \\",
      "  libibumad \\",
      "  rpm-build \\",
      "  perl \\",
      "  gcc \\",
      "  make \\",
      "  nfs-utils \\",
      "  chrony"
    ]
  }

  # Download and build Slurm
  provisioner "shell" {
    environment_vars = [
      "SLURM_VERSION=${var.slurm_version}",
      "SLURM_PREFIX=${var.slurm_prefix}"
    ]
    inline = [
      "echo '==> Downloading Slurm ${var.slurm_version}'",
      "cd /tmp",
      "wget -q https://download.schedmd.com/slurm/slurm-$${SLURM_VERSION}.tar.bz2",
      "tar xjf slurm-$${SLURM_VERSION}.tar.bz2",
      "cd slurm-$${SLURM_VERSION}",
      "",
      "echo '==> Building Slurm'",
      "./configure --prefix=$${SLURM_PREFIX} --sysconfdir=/etc/slurm",
      "make -j$(nproc)",
      "sudo make install",
      "",
      "echo '==> Verifying Slurm installation'",
      "$${SLURM_PREFIX}/sbin/slurmd --version",
      "",
      "echo '==> Cleaning up build files'",
      "cd /tmp",
      "rm -rf slurm-*"
    ]
  }

  # Create Slurm user and directories
  provisioner "shell" {
    inline = [
      "echo '==> Creating Slurm user and directories'",
      "sudo groupadd -g 1001 slurm || true",
      "sudo useradd -u 1001 -g slurm -s /bin/bash -d /var/lib/slurm slurm || true",
      "sudo mkdir -p /var/spool/slurm /var/log/slurm",
      "sudo chown -R slurm:slurm /var/spool/slurm /var/log/slurm",
      "sudo chmod 755 /var/spool/slurm /var/log/slurm"
    ]
  }

  # Configure NFS mount point
  provisioner "shell" {
    environment_vars = [
      "NFS_EXPORT=${var.nfs_export}"
    ]
    inline = [
      "echo '==> Configuring NFS mount point'",
      "sudo mkdir -p $${NFS_EXPORT}",
      "echo 'NFS will be mounted on boot via /etc/fstab entry added by user data or baked into AMI'"
    ]
  }

  # Retrieve and install Munge key
  provisioner "shell" {
    environment_vars = [
      "MUNGE_KEY_SECRET=${var.munge_key_secretsmanager}"
    ]
    inline = [
      "echo '==> Configuring Munge'",
      "sudo mkdir -p /etc/munge /var/log/munge /var/lib/munge",
      "sudo chown -R munge:munge /etc/munge /var/log/munge /var/lib/munge",
      "sudo chmod 0700 /etc/munge /var/log/munge /var/lib/munge",
      "",
      "# Note: Munge key will be retrieved at boot time from Secrets Manager",
      "# via user data or from S3. This ensures the key is never baked into the AMI.",
      "echo 'Munge key retrieval will happen at instance boot time'"
    ]
  }

  # Create script to get node name from EC2 tag
  provisioner "file" {
    content = <<-EOF
      #!/bin/bash
      # Get Slurm node name from EC2 instance tag using IMDSv2

      # Get IMDSv2 token
      TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
          -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
          -s --max-time 2)

      if [ -z "$TOKEN" ]; then
          echo "Error: Failed to retrieve IMDSv2 token"
          echo "$(hostname)"
          exit 0
      fi

      # Get Name tag from instance metadata
      NAME_TAG=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
          -s --max-time 2 \
          "http://169.254.169.254/latest/meta-data/tags/instance/Name")

      if [ -z "$NAME_TAG" ] || [ "$NAME_TAG" = "None" ]; then
          echo "$(hostname)"
      else
          echo "$NAME_TAG"
      fi
    EOF
    destination = "/tmp/get_slurm_nodename"
  }

  provisioner "shell" {
    inline = [
      "sudo mv /tmp/get_slurm_nodename /usr/local/bin/get_slurm_nodename",
      "sudo chmod +x /usr/local/bin/get_slurm_nodename"
    ]
  }

  # Create slurmd systemd service
  provisioner "file" {
    content = <<-EOF
      [Unit]
      Description=Slurm node daemon
      After=munge.service network.target remote-fs.target nfs.target
      Requires=munge.service

      [Service]
      Type=forking
      EnvironmentFile=-/etc/sysconfig/slurmd
      ExecStartPre=/bin/bash -c "/bin/systemctl set-environment SLURM_NODENAME=$(/usr/local/bin/get_slurm_nodename)"
      ExecStart=${var.slurm_prefix}/sbin/slurmd -N $SLURM_NODENAME $SLURMD_OPTIONS
      ExecReload=/bin/kill -HUP $MAINPID
      PIDFile=/var/run/slurmd.pid
      KillMode=process
      LimitNOFILE=131072
      LimitMEMLOCK=infinity
      LimitSTACK=infinity
      Delegate=yes

      [Install]
      WantedBy=multi-user.target
    EOF
    destination = "/tmp/slurmd.service"
  }

  provisioner "shell" {
    inline = [
      "sudo mv /tmp/slurmd.service /etc/systemd/system/slurmd.service",
      "sudo systemctl daemon-reload",
      "sudo systemctl enable slurmd",
      "sudo systemctl enable munge",
      "sudo systemctl enable chronyd"
    ]
  }

  # Create boot-time initialization script
  provisioner "file" {
    content = <<-EOF
      #!/bin/bash
      # Slurm compute node initialization script
      # This runs on first boot to complete configuration

      set -e

      HEADNODE_IP="${var.headnode_ip}"
      NFS_EXPORT="${var.nfs_export}"
      MUNGE_KEY_SECRET="${var.munge_key_secretsmanager}"
      AWS_REGION="${var.aws_region}"

      echo "==> Starting Slurm compute node initialization"

      # Configure NFS mount
      if ! grep -q "$HEADNODE_IP:$NFS_EXPORT" /etc/fstab; then
          echo "==> Configuring NFS mount"
          echo "$HEADNODE_IP:$NFS_EXPORT  $NFS_EXPORT  nfs  defaults,_netdev,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2  0 0" >> /etc/fstab
      fi

      # Mount NFS
      echo "==> Mounting NFS from headnode"
      mkdir -p $NFS_EXPORT
      mount $NFS_EXPORT || {
          echo "ERROR: Failed to mount NFS from $HEADNODE_IP:$NFS_EXPORT"
          echo "Check network connectivity and NFS exports on headnode"
          exit 1
      }

      # Retrieve Munge key from Secrets Manager
      echo "==> Retrieving Munge key from AWS Secrets Manager"
      aws secretsmanager get-secret-value \
        --secret-id $MUNGE_KEY_SECRET \
        --region $AWS_REGION \
        --query SecretBinary \
        --output text | base64 -d > /tmp/munge.key

      if [ ! -s /tmp/munge.key ]; then
          echo "ERROR: Failed to retrieve Munge key from Secrets Manager"
          exit 1
      fi

      # Install Munge key
      mv /tmp/munge.key /etc/munge/munge.key
      chown munge:munge /etc/munge/munge.key
      chmod 600 /etc/munge/munge.key

      # Start services
      echo "==> Starting Munge"
      systemctl start munge

      # Test Munge
      if ! munge -n | unmunge &>/dev/null; then
          echo "WARNING: Munge test failed"
      fi

      echo "==> Starting Slurmd"
      systemctl start slurmd

      echo "==> Slurm compute node initialization complete"
      echo "Node should now be visible in Slurm cluster"
    EOF
    destination = "/tmp/slurm-init.sh"
  }

  provisioner "shell" {
    inline = [
      "sudo mv /tmp/slurm-init.sh /usr/local/bin/slurm-init.sh",
      "sudo chmod +x /usr/local/bin/slurm-init.sh"
    ]
  }

  # Clean up for AMI
  provisioner "shell" {
    inline = [
      "echo '==> Cleaning up for AMI creation'",
      "sudo yum clean all",
      "sudo rm -rf /tmp/* /var/tmp/*",
      "sudo rm -f /root/.bash_history",
      "sudo rm -f /home/ec2-user/.bash_history",
      "sudo find /var/log -type f -exec truncate -s 0 {} \\;",
      "echo '==> AMI preparation complete'"
    ]
  }

  # Output AMI information
  post-processor "manifest" {
    output = "manifest.json"
    strip_path = true
  }
}
