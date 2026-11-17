#!/usr/bin/env bash
# 【parent】attestation / ETH 测试的统一入口。Sandbox 里的命令经 ssh 执行，parent 自己的命令用的是同一个实例角色。
#   bash parent/attest.sh restart normal|changed|debug  用原配置 / cmdline 多一个 quiet（PCR0 会变）/ debug 模式重启 Sandbox
#   bash parent/attest.sh probe                         NSM 探针：打印 PCR，调 KMS GenerateRandom(Recipient)
#   bash parent/attest.sh encrypt [文本] | decrypt | decrypt-noattest | parent-decrypt | seal
#   bash parent/attest.sh wallet-create | wallet-sign | wallet-noattest | parent-wallet-decrypt    attestation 保护的 ETH 钱包
#   bash parent/attest.sh eth-sign | parent-eth-sign    KMS Sign 签 ETH 交易（私钥不出 KMS，但 kms:Sign 没有 attestation）
#   bash parent/attest.sh eth-send | eth-receipt <交易哈希>   签名并广播到 Sepolia（地址里要有测试币）/ 查是否上链
#   bash parent/attest.sh stop                          停掉 Sandbox
set -uo pipefail
source "$(dirname "$0")/../config.sh"
cd "$WORK" && source "$WORK/env"
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
KEY=$ATTEST_KEY_ALIAS                                # attestation 用的 KMS key（create-attest-key.sh 创建）
ETH_KEY=$ETH_KEY_ALIAS                               # KMS Sign 用的 secp256k1 key（prereq.sh 创建）
RPC=${RPC:-https://ethereum-sepolia-rpc.publicnode.com}
SB=(ssh -n -i $WORK/id_rsa -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@172.16.0.2)
py() { "${SB[@]}" "cd /root && AWS_REGION=$REGION /opt/kms/bin/python $*"; }

case "${1:-}" in
  restart)
    case "${2:-normal}" in
      normal)  cp vm.json vm-run.json ;;
      changed) sed 's/net.ifnames=0/net.ifnames=0 quiet/' vm.json > vm-run.json; grep -q 'ifnames=0 quiet' vm-run.json ;;
      debug)   sed 's/"cpu_overcommit": false/"debug_mode": true, "cpu_overcommit": false/' vm.json > vm-run.json; grep -q debug_mode vm-run.json ;;
      *) echo "用法: bash parent/attest.sh restart normal|changed|debug"; exit 1 ;;
    esac
    "${SB[@]}" poweroff 2>/dev/null
    for i in $(seq 1 20); do pgrep -x firecracker >/dev/null || break; sleep 1; done
    sudo pkill -x firecracker; sudo rm -f /tmp/fc.sock
    sudo setsid nohup firecracker --api-sock /tmp/fc.sock --config-file vm-run.json > fc.log 2>&1 < /dev/null &
    for i in $(seq 1 60); do "${SB[@]}" true 2>/dev/null && { echo "Sandbox 已启动（$2）"; exit 0; }; sleep 2; done
    echo "Sandbox 没起来："; tail -5 fc.log; exit 1 ;;
  probe)            py nitro_attest.py ;;
  stop)             "${SB[@]}" poweroff 2>/dev/null; sleep 5; sudo pkill -x firecracker; pgrep -x firecracker >/dev/null || echo "Sandbox 已停" ;;
  encrypt)          py kms_attest_poc.py --key-id $KEY encrypt "$(printf '%q' "${2:-hello nitro sandbox}")" ;;
  decrypt)          py kms_attest_poc.py --key-id $KEY decrypt ;;
  decrypt-noattest) py kms_attest_poc.py --key-id $KEY decrypt --no-attestation ;;
  seal)             py kms_attest_poc.py --key-id $KEY seal ;;
  parent-decrypt)
    "${SB[@]}" cat /root/secret.b64 | base64 -d > /tmp/secret.bin
    aws kms decrypt --region $REGION --ciphertext-blob fileb:///tmp/secret.bin --query Plaintext --output text ;;
  wallet-create)    py kms_attest_wallet.py --key-id $KEY create ;;
  wallet-sign)      py kms_attest_wallet.py --key-id $KEY sign ;;
  wallet-noattest)  py kms_attest_wallet.py --key-id $KEY decrypt-noattest ;;
  parent-wallet-decrypt)
    "${SB[@]}" cat /root/wallet.json | python3 -c 'import json,sys,base64; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)["private_key_ciphertext"]))' > /tmp/wallet.bin
    aws kms decrypt --region $REGION --ciphertext-blob fileb:///tmp/wallet.bin \
      --encryption-context purpose=sandbox-eth-wallet --query Plaintext --output text ;;
  eth-sign)         py kms_sign_tx.py --key-id $ETH_KEY --to 0x000000000000000000000000000000000000dEaD --value-wei 1000000000000000 ;;
  eth-send)         py kms_sign_tx.py --key-id $ETH_KEY --rpc $RPC --to 0x000000000000000000000000000000000000dEaD --value-wei 1000000000000000 --send ;;
  eth-receipt)
    curl -s -H 'Content-Type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_getTransactionReceipt\",\"params\":[\"${2:?要交易哈希}\"]}" $RPC \
      | python3 -c 'import json,sys
r = json.load(sys.stdin)["result"]
if r is None: print("还没上链，过几秒再查")
else: b, st, fr = int(r["blockNumber"], 16), r["status"], r["from"]; print(f"区块 {b}  status {st}（0x1 = 成功）  from {fr}")' ;;
  parent-eth-sign)  # parent 用同一个角色直接让 KMS 签一个任意的 32 字节摘要
    aws kms sign --region $REGION --key-id $ETH_KEY --message-type DIGEST --signing-algorithm ECDSA_SHA_256 \
      --message "$(head -c 32 /dev/urandom | base64)" --query '[KeyId,SigningAlgorithm]' --output text ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
