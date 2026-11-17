#!/usr/bin/env bash
# 【工作机】开一台带 instance store 的 parent（默认 i7i.2xlarge：8 vCPU / 64 GiB / 1×1875 GB NVMe）
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
source "$REPO/config.sh"
for v in SUBNET_ID SG_ID KEY_NAME PEM; do
  [ -n "${!v:-}" ] && [[ "${!v}" != *xxxx* ]] || { echo "先在 workshop.env 里填 $v（cp workshop.env.example workshop.env）"; exit 1; }
done
# 实例角色给 attestation/ETH 测试用；Sandbox 经过 NAT 访问 IMDS 要多一跳，所以 hop limit 设 2
IID=$(aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.18-x86_64 \
  --instance-type "$INSTANCE_TYPE" --enclave-options Enabled=true \
  --iam-instance-profile Name="$ROLE_NAME" \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled \
  --key-name "$KEY_NAME" --subnet-id "$SUBNET_ID" --security-group-ids "$SG_ID" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=nitro-sandbox-workshop}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$IID"
read PRIV PUB <<<"$(aws ec2 describe-instances --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].[PrivateIpAddress,PublicIpAddress]' --output text)"
printf 'IID=%s\nPUBLIC_IP=%s\nPRIVATE_IP=%s\n' "$IID" "$PUB" "$PRIV" | tee "$REPO/.parent"
echo "大约 1 分钟后可以登录："
[ "$PUB" != "None" ] && echo "  从公网：ssh -i $PEM ec2-user@$PUB"
echo "  从同一个 VPC 里：ssh -i $PEM ec2-user@$PRIV"
