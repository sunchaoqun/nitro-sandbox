#!/usr/bin/env python3
"""attestation 保护的以太坊钱包：私钥只在 Sandbox 内存里，parent 拿不到。

create            KMS GenerateDataKeyPair(ECC_SECG_P256K1) + Recipient：KMS 只把私钥加密给 attestation 里的临时 RSA 公钥
                  （CiphertextForRecipient），在 Sandbox 里解开；wallet.json 只存地址、公钥和加密的私钥
sign              Decrypt(加密的私钥) + Recipient → 在 Sandbox 里签一笔 EIP-1559 交易，再独立验证发送方
decrypt-noattest  不带 attestation 解密私钥，用来验证 key policy 会拒绝

依赖同目录的 nitro_attest.py。
"""
import argparse
import base64
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_utils import keccak, to_checksum_address

import nitro_attest as na

CONTEXT = {"purpose": "sandbox-eth-wallet"}   # 加密上下文：解密时必须一致


def address_of(public_key):
    raw = public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return to_checksum_address(keccak(raw[1:])[-20:])


def with_attestation(call):
    """生成临时 RSA 密钥 → NSM attestation 文档（带公钥）→ 调 KMS → 在本地解开 CiphertextForRecipient。"""
    key, pub_der = na.new_recipient_key()
    doc = na.attestation_document(public_key_der=pub_der)
    pcrs = na.parse_attestation_document(doc)["pcrs"]
    print("本次 attestation 的 PCR0:", pcrs[0].hex()[:16] + "…", "（文档里有 PCR16 = 已封口）" if 16 in pcrs else "")
    resp = call({"KeyEncryptionAlgorithm": "RSAES_OAEP_SHA_256", "AttestationDocument": doc})
    if resp.get("Plaintext") or resp.get("PrivateKeyPlaintext"):
        raise SystemExit("KMS 返回了明文，说明它没有按 attestation 处理")
    return na.cms_decrypt(resp["CiphertextForRecipient"], key), resp


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key-id", default="alias/sandbox-attest-bench")
    p.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    p.add_argument("--wallet", default="wallet.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create")
    s = sub.add_parser("sign")
    s.add_argument("--to", default="0x000000000000000000000000000000000000dEaD")
    s.add_argument("--value-wei", type=int, default=10**15)
    s.add_argument("--chain-id", type=int, default=11155111, help="默认 Sepolia")
    s.add_argument("--nonce", type=int, default=0)
    sub.add_parser("decrypt-noattest")
    a = p.parse_args()
    if not a.region:
        raise SystemExit("需要 --region 或者环境变量 AWS_REGION")
    kms = boto3.client("kms", region_name=a.region)

    try:
        if a.cmd == "create":
            pkcs8, resp = with_attestation(lambda r: kms.generate_data_key_pair(
                KeyId=a.key_id, KeyPairSpec="ECC_SECG_P256K1", EncryptionContext=CONTEXT, Recipient=r))
            priv = serialization.load_der_private_key(pkcs8, password=None)
            assert isinstance(priv.curve, ec.SECP256K1)
            addr = address_of(priv.public_key())
            if addr != address_of(serialization.load_der_public_key(resp["PublicKey"])):
                raise SystemExit("私钥和 KMS 返回的公钥对不上")
            json.dump({"key_id": resp["KeyId"], "address": addr,
                       "public_key": base64.b64encode(resp["PublicKey"]).decode(),
                       "private_key_ciphertext": base64.b64encode(resp["PrivateKeyCiphertextBlob"]).decode()},
                      open(a.wallet, "w"), indent=1)
            print("钱包地址      :", addr)
            print(f"已保存 {a.wallet}：只有地址、公钥和加密的私钥，明文私钥没有写盘")
            return

        w = json.load(open(a.wallet))
        blob = base64.b64decode(w["private_key_ciphertext"])
        if a.cmd == "decrypt-noattest":
            kms.decrypt(CiphertextBlob=blob, KeyId=a.key_id, EncryptionContext=CONTEXT)
            print("没带 attestation 也解开了 ← key policy 没有生效！")
            sys.exit(1)

        pkcs8, resp = with_attestation(lambda r: kms.decrypt(
            CiphertextBlob=blob, KeyId=a.key_id, EncryptionContext=CONTEXT, Recipient=r))
        if resp["KeyId"] != w["key_id"]:
            raise SystemExit(f"KMS 用的 key 不对：{resp['KeyId']}")
        priv = serialization.load_der_private_key(pkcs8, password=None)
        acct = Account.from_key(priv.private_numbers().private_value.to_bytes(32, "big"))
        if acct.address != w["address"]:
            raise SystemExit("解出来的私钥和钱包地址对不上")
        tx = {"type": 2, "chainId": a.chain_id, "nonce": a.nonce, "to": to_checksum_address(a.to), "value": a.value_wei,
              "gas": 21000, "maxFeePerGas": 30 * 10**9, "maxPriorityFeePerGas": 10**9, "data": b""}
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        print("钱包地址      :", acct.address)
        print("交易哈希      :", "0x" + keccak(raw).hex())
        print("原始交易      :", "0x" + raw.hex()[:66] + "…")
        recovered = Account.recover_transaction(raw)
        print("eth-account 验证发送方:", recovered, "OK" if recovered == acct.address else "不一致！")
        if recovered != acct.address:
            sys.exit(1)
    except ClientError as err:
        e = err.response["Error"]
        print(f"KMS 拒绝: {e['Code']} - {e['Message']}")
        sys.exit(2)


if __name__ == "__main__":
    main()
