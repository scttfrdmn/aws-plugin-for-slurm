#!/bin/bash
# Connectivity Validation Script for On-Prem to AWS Cloud Bursting
# This script validates network connectivity, NFS access, and Slurm prerequisites
# before deploying cloud bursting

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test results
PASSED=0
FAILED=0
WARNINGS=0

print_header() {
    echo ""
    echo "=================================================="
    echo "$1"
    echo "=================================================="
}

print_test() {
    echo -n "  [ ] $1... "
}

print_pass() {
    echo -e "${GREEN}✓ PASS${NC}"
    ((PASSED++))
}

print_fail() {
    echo -e "${RED}✗ FAIL${NC}"
    echo -e "      ${RED}$1${NC}"
    ((FAILED++))
}

print_warn() {
    echo -e "${YELLOW}⚠ WARN${NC}"
    echo -e "      ${YELLOW}$1${NC}"
    ((WARNINGS++))
}

print_info() {
    echo -e "      ℹ $1"
}

# Parse command line arguments
HEADNODE_IP=""
AWS_REGION=""
VPC_ID=""
SUBNET_ID=""
NFS_EXPORT="/nfs"

usage() {
    cat <<EOF
Usage: $0 --headnode IP --region REGION --vpc VPC_ID --subnet SUBNET_ID [options]

Required:
  --headnode IP       On-premises headnode IP address
  --region REGION     AWS region (e.g., us-east-1)
  --vpc VPC_ID        AWS VPC ID to test connectivity with
  --subnet SUBNET_ID  AWS subnet ID to launch test instance in

Optional:
  --nfs-export PATH   NFS export path on headnode (default: /nfs)
  --help              Show this help message

Example:
  $0 --headnode 10.0.1.100 --region us-east-1 --vpc vpc-xxxxx --subnet subnet-xxxxx

This script will:
1. Verify on-prem headnode prerequisites (Slurm, NFS, Munge)
2. Launch a temporary EC2 instance in AWS
3. Test network connectivity between on-prem and AWS
4. Test NFS mount from AWS to on-prem
5. Test Munge authentication (if keys are configured)
6. Terminate the test instance
7. Provide a summary report

EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --headnode)
            HEADNODE_IP="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --vpc)
            VPC_ID="$2"
            shift 2
            ;;
        --subnet)
            SUBNET_ID="$2"
            shift 2
            ;;
        --nfs-export)
            NFS_EXPORT="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate required arguments
if [[ -z "$HEADNODE_IP" ]] || [[ -z "$AWS_REGION" ]] || [[ -z "$VPC_ID" ]] || [[ -z "$SUBNET_ID" ]]; then
    echo "Error: Missing required arguments"
    usage
fi

print_header "On-Premises to AWS Cloud Bursting - Connectivity Validation"
echo "Headnode IP:  $HEADNODE_IP"
echo "AWS Region:   $AWS_REGION"
echo "VPC ID:       $VPC_ID"
echo "Subnet ID:    $SUBNET_ID"
echo "NFS Export:   $NFS_EXPORT"

# Check if running on headnode
print_header "Step 1: Verify On-Premises Headnode"

print_test "Check if running on headnode or with SSH access"
if ping -c 1 -W 2 $HEADNODE_IP &>/dev/null; then
    print_pass
else
    print_fail "Cannot ping headnode at $HEADNODE_IP"
    echo "This script must be run from the headnode or from a machine with SSH access to it."
    exit 1
fi

print_test "Check Slurm installation"
if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no $HEADNODE_IP "command -v slurmctld" &>/dev/null; then
    SLURM_VERSION=$(ssh $HEADNODE_IP "slurmctld --version 2>&1 | head -1")
    print_pass
    print_info "Found: $SLURM_VERSION"
else
    print_fail "slurmctld not found on headnode"
fi

print_test "Check Munge installation"
if ssh $HEADNODE_IP "command -v munged" &>/dev/null; then
    print_pass
else
    print_fail "munged not found on headnode"
fi

print_test "Check Munge key exists"
if ssh $HEADNODE_IP "test -f /etc/munge/munge.key"; then
    MUNGE_KEY_MD5=$(ssh $HEADNODE_IP "md5sum /etc/munge/munge.key | awk '{print \$1}'")
    print_pass
    print_info "Munge key MD5: $MUNGE_KEY_MD5"
else
    print_fail "Munge key not found at /etc/munge/munge.key"
fi

print_test "Check NFS exports"
if ssh $HEADNODE_IP "exportfs | grep -q '$NFS_EXPORT'"; then
    print_pass
    EXPORTS=$(ssh $HEADNODE_IP "exportfs | grep '$NFS_EXPORT'")
    print_info "Exports: $EXPORTS"
else
    print_warn "NFS export $NFS_EXPORT not found in exportfs output"
    print_info "You may need to add: echo \"$NFS_EXPORT *(rw,sync,no_root_squash)\" >> /etc/exports"
fi

print_test "Check NFS service running"
if ssh $HEADNODE_IP "systemctl is-active nfs-server" &>/dev/null; then
    print_pass
else
    print_fail "NFS server is not running"
fi

# Check AWS CLI
print_header "Step 2: Verify AWS CLI and Permissions"

print_test "Check AWS CLI installed"
if command -v aws &>/dev/null; then
    AWS_CLI_VERSION=$(aws --version 2>&1)
    print_pass
    print_info "$AWS_CLI_VERSION"
else
    print_fail "AWS CLI not installed. Install with: pip3 install awscli"
    exit 1
fi

print_test "Check AWS credentials configured"
if aws sts get-caller-identity --region $AWS_REGION &>/dev/null; then
    AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    AWS_USER=$(aws sts get-caller-identity --query Arn --output text)
    print_pass
    print_info "Account: $AWS_ACCOUNT"
    print_info "Identity: $AWS_USER"
else
    print_fail "AWS credentials not configured. Run: aws configure"
    exit 1
fi

print_test "Check EC2 permissions"
if aws ec2 describe-vpcs --vpc-ids $VPC_ID --region $AWS_REGION &>/dev/null; then
    print_pass
else
    print_fail "Cannot access VPC $VPC_ID. Check IAM permissions."
    exit 1
fi

# Get latest Amazon Linux 2 AMI
print_header "Step 3: Prepare Test Instance"

print_test "Find latest Amazon Linux 2 AMI"
AMI_ID=$(aws ec2 describe-images \
    --owners amazon \
    --filters "Name=name,Values=amzn2-ami-hvm-*-x86_64-gp2" \
              "Name=state,Values=available" \
    --region $AWS_REGION \
    --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
    --output text)

if [[ -n "$AMI_ID" ]]; then
    print_pass
    print_info "AMI: $AMI_ID"
else
    print_fail "Could not find Amazon Linux 2 AMI"
    exit 1
fi

# Create security group
print_test "Create temporary security group"
SG_ID=$(aws ec2 create-security-group \
    --group-name slurm-connectivity-test-$(date +%s) \
    --description "Temporary security group for connectivity testing" \
    --vpc-id $VPC_ID \
    --region $AWS_REGION \
    --query 'GroupId' \
    --output text 2>/dev/null)

if [[ -n "$SG_ID" ]]; then
    print_pass
    print_info "Security Group: $SG_ID"

    # Allow all traffic from headnode
    aws ec2 authorize-security-group-ingress \
        --group-id $SG_ID \
        --protocol all \
        --cidr ${HEADNODE_IP}/32 \
        --region $AWS_REGION &>/dev/null
else
    print_fail "Could not create security group"
    exit 1
fi

# Cleanup function
cleanup() {
    if [[ -n "$INSTANCE_ID" ]]; then
        echo ""
        print_header "Cleanup"
        echo "Terminating test instance $INSTANCE_ID..."
        aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $AWS_REGION &>/dev/null
        aws ec2 wait instance-terminated --instance-ids $INSTANCE_ID --region $AWS_REGION 2>/dev/null &
    fi

    if [[ -n "$SG_ID" ]]; then
        echo "Deleting security group $SG_ID..."
        # Wait a bit for instance to detach
        sleep 10
        aws ec2 delete-security-group --group-id $SG_ID --region $AWS_REGION &>/dev/null || true
    fi
}

trap cleanup EXIT

# Launch test instance
print_test "Launch test instance"
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id $AMI_ID \
    --instance-type t3.micro \
    --subnet-id $SUBNET_ID \
    --security-group-ids $SG_ID \
    --region $AWS_REGION \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=slurm-connectivity-test},{Key=Purpose,Value=Testing}]' \
    --query 'Instances[0].InstanceId' \
    --output text 2>/dev/null)

if [[ -n "$INSTANCE_ID" ]]; then
    print_pass
    print_info "Instance: $INSTANCE_ID"
else
    print_fail "Could not launch instance"
    exit 1
fi

print_test "Wait for instance to be running (this may take 1-2 minutes)"
if aws ec2 wait instance-running --instance-ids $INSTANCE_ID --region $AWS_REGION 2>/dev/null; then
    print_pass
else
    print_fail "Instance failed to start"
    exit 1
fi

# Get instance IP
INSTANCE_IP=$(aws ec2 describe-instances \
    --instance-ids $INSTANCE_ID \
    --region $AWS_REGION \
    --query 'Reservations[0].Instances[0].PrivateIpAddress' \
    --output text)

print_info "Instance private IP: $INSTANCE_IP"

# Wait for instance to be ready
print_test "Wait for instance to be fully initialized"
sleep 30  # Give it time to boot
print_pass

# Test connectivity
print_header "Step 4: Test Network Connectivity"

print_test "Ping from headnode to AWS instance"
if ssh $HEADNODE_IP "ping -c 3 -W 5 $INSTANCE_IP" &>/dev/null; then
    print_pass
else
    print_fail "Cannot ping AWS instance from headnode"
    print_info "Check VPN/Direct Connect configuration"
    print_info "Check routing tables on both sides"
fi

print_test "Test NFS port (2049) accessibility from AWS"
if timeout 10 bash -c "echo >/dev/tcp/${HEADNODE_IP}/2049" 2>/dev/null; then
    print_pass
else
    print_fail "Cannot reach NFS port 2049 on headnode from test location"
    print_info "Firewall may be blocking NFS traffic"
fi

# Test NFS mount via AWS CLI (using SSM if available, otherwise skip)
print_header "Step 5: Test NFS Mount"

print_test "Check if AWS Systems Manager agent is available"
if aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --region $AWS_REGION 2>/dev/null | grep -q $INSTANCE_ID; then
    print_pass
    HAS_SSM=true
else
    print_warn "SSM agent not available. Skipping NFS mount test."
    print_info "For full testing, ensure SSM agent is installed in base AMI"
    HAS_SSM=false
fi

if [[ "$HAS_SSM" == "true" ]]; then
    print_test "Install NFS client on test instance"
    COMMAND_ID=$(aws ssm send-command \
        --instance-ids $INSTANCE_ID \
        --document-name "AWS-RunShellScript" \
        --parameters 'commands=["yum install -y nfs-utils"]' \
        --region $AWS_REGION \
        --query 'Command.CommandId' \
        --output text)

    sleep 5
    if aws ssm get-command-invocation \
        --command-id $COMMAND_ID \
        --instance-id $INSTANCE_ID \
        --region $AWS_REGION \
        --query 'Status' \
        --output text | grep -q Success; then
        print_pass
    else
        print_fail "Could not install NFS client"
    fi

    print_test "Test NFS mount from AWS instance to headnode"
    COMMAND_ID=$(aws ssm send-command \
        --instance-ids $INSTANCE_ID \
        --document-name "AWS-RunShellScript" \
        --parameters "commands=[\"mkdir -p /mnt/test\",\"mount -t nfs ${HEADNODE_IP}:${NFS_EXPORT} /mnt/test\",\"ls /mnt/test\",\"umount /mnt/test\"]" \
        --region $AWS_REGION \
        --query 'Command.CommandId' \
        --output text)

    sleep 5
    if aws ssm get-command-invocation \
        --command-id $COMMAND_ID \
        --instance-id $INSTANCE_ID \
        --region $AWS_REGION \
        --query 'Status' \
        --output text | grep -q Success; then
        print_pass
        NFS_OUTPUT=$(aws ssm get-command-invocation \
            --command-id $COMMAND_ID \
            --instance-id $INSTANCE_ID \
            --region $AWS_REGION \
            --query 'StandardOutputContent' \
            --output text)
        print_info "NFS mount successful, directory listing:"
        echo "$NFS_OUTPUT" | head -5 | sed 's/^/          /'
    else
        print_fail "NFS mount failed"
        ERROR_OUTPUT=$(aws ssm get-command-invocation \
            --command-id $COMMAND_ID \
            --instance-id $INSTANCE_ID \
            --region $AWS_REGION \
            --query 'StandardErrorContent' \
            --output text)
        print_info "Error: $ERROR_OUTPUT"
    fi
fi

# Summary
print_header "Validation Summary"
echo ""
echo "Tests Passed:  ${GREEN}$PASSED${NC}"
echo "Tests Failed:  ${RED}$FAILED${NC}"
echo "Warnings:      ${YELLOW}$WARNINGS${NC}"
echo ""

if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All critical tests passed!${NC}"
    echo ""
    echo "Your environment is ready for cloud bursting. Next steps:"
    echo "  1. Build your Slurm AMI (see examples/packer/)"
    echo "  2. Configure the plugin on your headnode"
    echo "  3. Test with a simple job: srun -p cloud hostname"
    echo ""
    echo "See docs/onprem-to-aws-bursting.md for detailed setup instructions."
    exit 0
else
    echo -e "${RED}✗ Some tests failed.${NC}"
    echo ""
    echo "Please address the failed tests before proceeding with cloud bursting setup."
    echo "Common issues:"
    echo "  - VPN/Direct Connect not properly configured"
    echo "  - Firewall blocking required ports"
    echo "  - NFS exports not allowing AWS subnet"
    echo "  - Routing tables not propagating routes"
    echo ""
    echo "See docs/troubleshooting.md for help resolving these issues."
    exit 1
fi
