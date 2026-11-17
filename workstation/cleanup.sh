#!/usr/bin/env bash
# 【工作机】清理：删 parent 实例，attestation key 7 天后删除。
#   bash workstation/cleanup.sh          只删实例和 attestation key
#   bash workstation/cleanup.sh --all    连 ETH key 和实例角色也删
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
source "$REPO/config.sh"
[ -f "$REPO/.parent" ] && source "$REPO/.parent"
if [ -n "${IID:-}" ]; then
  aws ec2 terminate-instances --instance-ids "$IID" --query 'TerminatingInstances[0].CurrentState.Name' --output text
fi
del_key() {   # 删除要用 KeyId，别名不行；7 天内可以 aws kms cancel-key-deletion 撤销
  local id; id=$(aws kms describe-key --key-id "$1" --query KeyMetadata.KeyId --output text 2>/dev/null) || { echo "$1 不存在"; return; }
  aws kms delete-alias --alias-name "$1"
  aws kms schedule-key-deletion --key-id "$id" --pending-window-in-days 7 --query DeletionDate --output text
}
del_key "$ATTEST_KEY_ALIAS"
if [ "${1:-}" = "--all" ]; then
  del_key "$ETH_KEY_ALIAS"
  aws iam remove-role-from-instance-profile --instance-profile-name "$ROLE_NAME" --role-name "$ROLE_NAME" 2>/dev/null
  aws iam delete-instance-profile --instance-profile-name "$ROLE_NAME" 2>/dev/null
  for p in $(aws iam list-role-policies --role-name "$ROLE_NAME" --query 'PolicyNames' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$p"
  done
  aws iam delete-role --role-name "$ROLE_NAME" 2>/dev/null && echo "角色 $ROLE_NAME 已删除"
fi
rm -f "$REPO/.parent"
echo "清理完成"
