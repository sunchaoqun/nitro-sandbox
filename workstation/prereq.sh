#!/usr/bin/env bash
# 【工作机】实例角色 + ETH 签名 key。已经有了就跳过，可以重复执行。
set -euo pipefail
source "$(dirname "$0")/../config.sh"
ACCT=$(aws sts get-caller-identity --query Account --output text)
echo "账号 $ACCT，区域 $AWS_REGION"

# 1) KMS 里的 secp256k1 签名 key：私钥不出 KMS，用 kms:Sign 签以太坊交易
if aws kms describe-key --key-id "$ETH_KEY_ALIAS" >/dev/null 2>&1; then
  echo "ETH key 已存在：$ETH_KEY_ALIAS"
else
  KEY_ID=$(aws kms create-key --key-spec ECC_SECG_P256K1 --key-usage SIGN_VERIFY \
    --description "nitro sandbox workshop: eth signer" --query KeyMetadata.KeyId --output text)
  aws kms create-alias --alias-name "$ETH_KEY_ALIAS" --target-key-id "$KEY_ID"
  echo "ETH key 已创建：$ETH_KEY_ALIAS（$KEY_ID）"
fi
ETH_KEY_ARN=$(aws kms describe-key --key-id "$ETH_KEY_ALIAS" --query KeyMetadata.Arn --output text)

# 2) 实例角色：只能对这把 ETH key 做 Sign/GetPublicKey，外加 GenerateRandom（NSM 探针用）
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "角色已存在：$ROLE_NAME"
else
  aws iam create-role --role-name "$ROLE_NAME" --query Role.Arn --output text --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name kms-eth-sign --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"kms:Sign\",\"kms:GetPublicKey\"],\"Resource\":\"$ETH_KEY_ARN\"}]}"
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name kms-generate-random --policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"kms:GenerateRandom","Resource":"*"}]}'
if ! aws iam get-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE_NAME" --role-name "$ROLE_NAME"
  echo "instance profile 已创建，等 15 秒让它生效"; sleep 15
fi
echo "角色策略：$(aws iam list-role-policies --role-name "$ROLE_NAME" --query 'PolicyNames' --output text)"
echo "完成"
