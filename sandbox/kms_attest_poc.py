#!/usr/bin/env python3
"""Nitro Sandbox + KMS attestation POC：在 Sandbox 里加密数据，再带 attestation 解密。

encrypt                  普通的 KMS Encrypt（不需要 attestation）
decrypt                  NSM 生成 attestation 文档（带临时 RSA 公钥）→ KMS Decrypt(Recipient) → 本地解开 CiphertextForRecipient
decrypt --no-attestation 不带 attestation 直接 Decrypt，用来验证 key policy 会拒绝
pcr16                    查看 PCR16
seal                     封口：扩展并锁定 PCR16。attestation 文档只包含已锁定的 PCR，key policy 规定
                         "文档里出现 PCR16 就拒绝"，所以封口后直到重启都拿不到 KMS 的东西

依赖同目录的 nitro_attest.py。
"""
import argparse
import base64
import hashlib
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

import nitro_attest as na


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key-id", default="alias/sandbox-attest-poc")
    p.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    sub = p.add_subparsers(dest="cmd", required=True)
    enc = sub.add_parser("encrypt")
    enc.add_argument("text")
    enc.add_argument("--out", default="secret.b64")
    dec = sub.add_parser("decrypt")
    dec.add_argument("--in", dest="infile", default="secret.b64")
    dec.add_argument("--no-attestation", action="store_true")
    sub.add_parser("pcr16")
    sub.add_parser("seal")
    a = p.parse_args()

    if a.cmd == "pcr16":
        data, locked = na.describe_pcr(16)
        print(f"PCR16: {data.hex()}  lock={locked}  {'已封口' if locked else '未封口'}")
        return
    if a.cmd == "seal":
        before, _ = na.describe_pcr(16)
        after = na.extend_pcr(16, hashlib.sha384(f"sealed {time.time()}".encode()).digest())
        na.lock_pcr(16)
        print("PCR16 封口前:", before.hex())
        print("PCR16 封口后:", after.hex(), "（已锁定，不能再改）")
        print("从现在起到重启前，这个 Sandbox 的 attestation 都不再满足 key policy")
        return

    if not a.region:
        raise SystemExit("需要 --region 或者环境变量 AWS_REGION")
    kms = boto3.client("kms", region_name=a.region)

    if a.cmd == "encrypt":
        resp = kms.encrypt(KeyId=a.key_id, Plaintext=a.text.encode())
        with open(a.out, "w") as f:
            f.write(base64.b64encode(resp["CiphertextBlob"]).decode())
        print(f"已加密 → {a.out}（KeyId: {resp['KeyId']}）")
        return

    blob = base64.b64decode(open(a.infile).read())
    try:
        if a.no_attestation:
            resp = kms.decrypt(CiphertextBlob=blob, KeyId=a.key_id)
            print("没带 attestation 也解开了:", resp["Plaintext"].decode(), "← key policy 没有生效！")
            sys.exit(1)
        key, pub_der = na.new_recipient_key()
        doc = na.attestation_document(public_key_der=pub_der)
        pcrs = na.parse_attestation_document(doc)["pcrs"]
        print("本次 attestation 的 PCR0 :", pcrs[0].hex())
        print("本次 attestation 的 PCR16:", pcrs[16].hex() + "（已封口）" if 16 in pcrs else "不在文档里（未封口）")
        resp = kms.decrypt(CiphertextBlob=blob, KeyId=a.key_id, Recipient={
            "KeyEncryptionAlgorithm": "RSAES_OAEP_SHA_256", "AttestationDocument": doc})
    except ClientError as err:
        e = err.response["Error"]
        print(f"KMS 拒绝: {e['Code']} - {e['Message']}")
        sys.exit(2)
    if resp.get("Plaintext"):
        raise SystemExit("KMS 返回了明文，说明它没有按 attestation 处理")
    print("KeyId   :", resp["KeyId"])
    print("解密结果:", na.cms_decrypt(resp["CiphertextForRecipient"], key).decode())


if __name__ == "__main__":
    main()
