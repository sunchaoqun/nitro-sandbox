#!/usr/bin/env python3
"""用 KMS 里的 secp256k1 密钥签一笔 EIP-1559 交易。私钥不出 KMS。

默认只签名不广播；给了 --rpc 会自动填 nonce/chainId/gas 价格，再加 --send 才广播。
"""
import argparse
import json
import os
import urllib.request

import boto3
import rlp
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak, to_checksum_address

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def kms_public_key(kms, key_id):
    der = kms.get_public_key(KeyId=key_id)["PublicKey"]
    # SubjectPublicKeyInfo 的最后 65 字节是未压缩点 04||X||Y
    point = der[-65:]
    if point[0] != 0x04:
        raise ValueError("不是未压缩的 EC 公钥，确认 KeySpec 是 ECC_SECG_P256K1")
    return keys.PublicKey(point[1:])


def der_to_rs(sig):
    # ECDSA-Sig-Value ::= SEQUENCE { r INTEGER, s INTEGER }，长度 <= 72，都是短格式
    if sig[0] != 0x30 or sig[2] != 0x02:
        raise ValueError("签名不是 DER SEQUENCE")
    r_len = sig[3]
    r = int.from_bytes(sig[4:4 + r_len], "big")
    off = 4 + r_len
    if sig[off] != 0x02:
        raise ValueError("签名里 s 不是 INTEGER")
    s_len = sig[off + 1]
    s = int.from_bytes(sig[off + 2:off + 2 + s_len], "big")
    return r, s


def kms_sign_digest(kms, key_id, digest, pub):
    sig = kms.sign(KeyId=key_id, Message=digest, MessageType="DIGEST",
                   SigningAlgorithm="ECDSA_SHA_256")["Signature"]
    r, s = der_to_rs(sig)
    # EIP-2：以太坊只接受 low-s，KMS 不保证
    if s > SECP256K1_N // 2:
        s = SECP256K1_N - s
    # KMS 不返回 recovery id，两个都试一下，能恢复出同一个公钥的就是 v
    for v in (0, 1):
        if keys.Signature(vrs=(v, r, s)).recover_public_key_from_msg_hash(digest) == pub:
            return v, r, s
    raise RuntimeError("恢复不出 KMS 公钥，签名有问题")


def rpc_call(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    # 公共节点（如 publicnode）会对 Python-urllib 的默认 UA 返回 403
    req = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                             "User-Agent": "kms-sign-tx/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        out = json.load(resp)
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key-id", default=os.environ.get("KMS_KEY_ID", "alias/nitro-sandbox-eth"))
    # boto3 只认 AWS_DEFAULT_REGION，不认 AWS_REGION，这里两个都接受
    p.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    p.add_argument("--to", required=True)
    p.add_argument("--value-wei", type=int, default=0)
    p.add_argument("--data", default="0x")
    p.add_argument("--chain-id", type=int, default=11155111, help="默认 Sepolia")
    p.add_argument("--nonce", type=int, default=0)
    p.add_argument("--gas", type=int, default=21000)
    p.add_argument("--max-fee-wei", type=int, default=30 * 10**9)
    p.add_argument("--priority-fee-wei", type=int, default=1 * 10**9)
    p.add_argument("--rpc", help="以太坊 JSON-RPC，给了就从链上取 nonce/chainId/gas 价格")
    p.add_argument("--send", action="store_true", help="签完广播（需要 --rpc）")
    a = p.parse_args()

    if not a.region:
        raise SystemExit("需要 --region 或者环境变量 AWS_REGION")
    kms = boto3.client("kms", region_name=a.region)
    pub = kms_public_key(kms, a.key_id)
    sender = pub.to_checksum_address()
    print("KMS 地址      :", sender)

    if a.rpc:
        a.chain_id = int(rpc_call(a.rpc, "eth_chainId", []), 16)
        a.nonce = int(rpc_call(a.rpc, "eth_getTransactionCount", [sender, "pending"]), 16)
        a.priority_fee_wei = int(rpc_call(a.rpc, "eth_maxPriorityFeePerGas", []), 16)
        base = int(rpc_call(a.rpc, "eth_getBlockByNumber", ["latest", False])["baseFeePerGas"], 16)
        a.max_fee_wei = 2 * base + a.priority_fee_wei
        # 不能写死 21000：实测 Sepolia 上转给一个从没用过的新地址要 ~20 万 gas，给少了交易会失败（手续费照扣）。
        # 估算后多留 10%，没用完的会退回
        est = int(rpc_call(a.rpc, "eth_estimateGas", [{"from": sender, "to": to_checksum_address(a.to),
                                                       "value": hex(a.value_wei), "data": a.data}]), 16)
        a.gas = est * 11 // 10
        print("gas 上限      :", a.gas, f"（估算 {est}）")

    to = bytes.fromhex(to_checksum_address(a.to)[2:])
    data = bytes.fromhex(a.data[2:] if a.data.startswith("0x") else a.data)
    # EIP-1559: 0x02 || rlp([chainId, nonce, maxPriorityFee, maxFee, gas, to, value, data, accessList])
    fields = [a.chain_id, a.nonce, a.priority_fee_wei, a.max_fee_wei, a.gas, to, a.value_wei, data, []]
    digest = keccak(b"\x02" + rlp.encode(fields))
    print("签名哈希      :", "0x" + digest.hex())

    v, r, s = kms_sign_digest(kms, a.key_id, digest, pub)
    raw = b"\x02" + rlp.encode(fields + [v, r, s])
    raw_hex = "0x" + raw.hex()
    print("交易哈希      :", "0x" + keccak(raw).hex())
    print("原始交易      :", raw_hex)

    # 用 eth-account 独立解码一遍，确认发送方就是 KMS 地址
    recovered = Account.recover_transaction(raw_hex)
    print("eth-account 验证发送方:", recovered, "OK" if recovered == sender else "不一致！")
    if recovered != sender:
        raise SystemExit(1)

    if a.send:
        if not a.rpc:
            raise SystemExit("--send 需要 --rpc")
        print("已广播        :", rpc_call(a.rpc, "eth_sendRawTransaction", [raw_hex]))


if __name__ == "__main__":
    main()
