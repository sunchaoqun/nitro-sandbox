#!/usr/bin/env bash
# 【工作机】创建（或更新）只认这个 PCR0 的 KMS key。用法：bash workstation/create-attest-key.sh <PCR0>
# 别名已经存在时只更新 key policy：换了内核/initrd/cmdline、PCR0 变了的时候也用它。
set -euo pipefail
source "$(dirname "$0")/../config.sh"
PCR0=${1:-}
[[ "$PCR0" =~ ^[0-9a-f]{96}$ ]] || { echo "用法：bash workstation/create-attest-key.sh <PCR0（96 个十六进制字符）>"; exit 1; }
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)
ACCT=$(echo "$ROLE_ARN" | cut -d: -f5)
POLICY=$(mktemp)
cat > "$POLICY" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "KeyAdministration", "Effect": "Allow", "Principal": { "AWS": "arn:aws:iam::$ACCT:root" },
      "NotAction": ["kms:Decrypt", "kms:ReEncryptFrom", "kms:CreateGrant"], "Resource": "*" },
    { "Sid": "EncryptWithoutAttestation", "Effect": "Allow",
      "Principal": { "AWS": ["arn:aws:iam::$ACCT:root", "$ROLE_ARN"] }, "Action": "kms:Encrypt", "Resource": "*" },
    { "Sid": "AttestedSandboxOnly", "Effect": "Allow", "Principal": { "AWS": "$ROLE_ARN" },
      "Action": ["kms:Decrypt", "kms:GenerateDataKeyPair"], "Resource": "*",
      "Condition": { "StringEqualsIgnoreCase": { "kms:RecipientAttestation:PCR0": "$PCR0" } } },
    { "Sid": "DenyDecryptUnlessPCR0Matches", "Effect": "Deny", "Principal": "*",
      "Action": ["kms:Decrypt", "kms:ReEncryptFrom"], "Resource": "*",
      "Condition": { "StringNotEqualsIgnoreCase": { "kms:RecipientAttestation:PCR0": "$PCR0" } } },
    { "Sid": "DenyAfterSeal", "Effect": "Deny", "Principal": "*",
      "Action": ["kms:Decrypt", "kms:ReEncryptFrom", "kms:GenerateDataKeyPair"], "Resource": "*",
      "Condition": { "Null": { "kms:RecipientAttestation:PCR16": "false" } } }
  ]
}
JSON
FINDINGS=$(aws accessanalyzer validate-policy --policy-type RESOURCE_POLICY --policy-document "file://$POLICY" \
  --query 'findings[].[findingType,issueCode]' --output text)
[ -z "$FINDINGS" ] && echo "Access Analyzer：策略没有问题" || { echo "Access Analyzer 报告：$FINDINGS"; exit 1; }
if KEY_ID=$(aws kms describe-key --key-id "$ATTEST_KEY_ALIAS" --query KeyMetadata.KeyId --output text 2>/dev/null); then
  aws kms put-key-policy --key-id "$KEY_ID" --policy-name default --policy "file://$POLICY"   # 这里不能用别名
  echo "已更新 $ATTEST_KEY_ALIAS 的 key policy（PCR0=${PCR0:0:16}…）"
else
  KEY_ID=$(aws kms create-key --description "nitro sandbox workshop: attestation (PCR0)" \
    --policy "file://$POLICY" --query KeyMetadata.KeyId --output text)
  aws kms create-alias --alias-name "$ATTEST_KEY_ALIAS" --target-key-id "$KEY_ID"
  echo "已创建 $ATTEST_KEY_ALIAS（$KEY_ID），只认 PCR0=${PCR0:0:16}…"
fi
rm -f "$POLICY"
