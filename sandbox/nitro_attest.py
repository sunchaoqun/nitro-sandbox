#!/usr/bin/env python3
"""Nitro Sandbox 里的 attestation 工具：向 /dev/nsm 要 attestation 文档、解析 PCR、
解开 KMS 用 Recipient 返回的 CiphertextForRecipient（CMS EnvelopedData）。

直接运行就是可行性探针：拿 attestation 文档 → 打印 PCR → 调 KMS GenerateRandom(Recipient)
→ 在本地解开结果。能跑通说明 NSM 可用，而且 KMS 接受这台 Sandbox 的 attestation 文档。
"""
import argparse
import ctypes
import fcntl
import os

import cbor2
from asn1crypto import cms
from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# include/uapi/linux/nsm.h：_IOWR(0x0A, 0, struct nsm_raw)，struct nsm_raw 是两个 {u64 addr; u64 len}
NSM_IOCTL_RAW = (3 << 30) | (32 << 16) | (0x0A << 8) | 0
NSM_RESPONSE_MAX_SIZE = 0x3000


def nsm_request(request, device="/dev/nsm"):
    req = cbor2.dumps(request)
    req_buf = ctypes.create_string_buffer(req, len(req))
    resp_buf = ctypes.create_string_buffer(NSM_RESPONSE_MAX_SIZE)
    msg = (ctypes.c_uint64 * 4)(ctypes.addressof(req_buf), len(req),
                                ctypes.addressof(resp_buf), NSM_RESPONSE_MAX_SIZE)
    fd = os.open(device, os.O_RDWR)
    try:
        fcntl.ioctl(fd, NSM_IOCTL_RAW, msg, True)
    finally:
        os.close(fd)
    return cbor2.loads(resp_buf.raw[:msg[3]])


def attestation_document(public_key_der=None, user_data=None, nonce=None):
    resp = nsm_request({"Attestation": {"user_data": user_data, "nonce": nonce,
                                        "public_key": public_key_der}})
    if "Attestation" not in resp:
        raise RuntimeError(f"NSM 返回错误: {resp}")
    return resp["Attestation"]["document"]


def describe_pcr(index):
    resp = nsm_request({"DescribePCR": {"index": index}})
    if "DescribePCR" not in resp:
        raise RuntimeError(f"NSM 返回错误: {resp}")
    return resp["DescribePCR"]["data"], resp["DescribePCR"]["lock"]


def extend_pcr(index, data):
    # PCR16～31 可以在运行时扩展：new = SHA384(old || data)，不能撤销，重启才清零
    resp = nsm_request({"ExtendPCR": {"index": index, "data": data}})
    if "ExtendPCR" not in resp:
        raise RuntimeError(f"NSM 返回错误: {resp}")
    return resp["ExtendPCR"]["data"]


def lock_pcr(index):
    # 锁定后不能再扩展；attestation 文档只包含已锁定的 PCR
    resp = nsm_request({"LockPCR": {"index": index}})
    if resp != "LockPCR" and not (isinstance(resp, dict) and "LockPCR" in resp):
        raise RuntimeError(f"NSM 返回错误: {resp}")


def parse_attestation_document(doc):
    # COSE_Sign1：[protected, unprotected, payload, signature]，可能带 CBOR tag 18
    cose = cbor2.loads(doc)
    if isinstance(cose, cbor2.CBORTag):
        cose = cose.value
    return cbor2.loads(cose[2])


def new_recipient_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = key.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, pub_der


def cms_decrypt(ciphertext_for_recipient, private_key):
    """解开 KMS 的 CiphertextForRecipient：RSAES-OAEP-SHA256 包着内容密钥，内容用 AES-CBC 加密。"""
    info = cms.ContentInfo.load(ciphertext_for_recipient)
    if info["content_type"].native != "enveloped_data":
        raise ValueError(f"不是 EnvelopedData: {info['content_type'].native}")
    env = info["content"]
    ktri = env["recipient_infos"][0].chosen
    kea = ktri["key_encryption_algorithm"]
    if kea["algorithm"].native != "rsaes_oaep":
        raise ValueError(f"不支持的密钥加密算法: {kea['algorithm'].native}")
    cek = private_key.decrypt(ktri["encrypted_key"].native, asym_padding.OAEP(
        mgf=asym_padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    eci = env["encrypted_content_info"]
    alg = eci["content_encryption_algorithm"]
    if alg["algorithm"].native not in ("aes128_cbc", "aes192_cbc", "aes256_cbc"):
        raise ValueError(f"不支持的内容加密算法: {alg['algorithm'].dotted}")
    dec = Cipher(algorithms.AES(cek), modes.CBC(alg["parameters"].native)).decryptor()
    padded = dec.update(eci["encrypted_content"].native) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    p.add_argument("--skip-kms", action="store_true", help="只测 NSM，不调 KMS")
    a = p.parse_args()

    key, pub_der = new_recipient_key()
    doc = attestation_document(public_key_der=pub_der)
    payload = parse_attestation_document(doc)
    print(f"attestation 文档: {len(doc)} 字节, module_id={payload.get('module_id')}, digest={payload.get('digest')}")
    for idx in sorted(payload["pcrs"]):
        val = payload["pcrs"][idx]
        if any(val):
            print(f"  PCR{idx:<2} {val.hex()}")
    zero = [i for i, v in payload["pcrs"].items() if not any(v)]
    print(f"  全零的 PCR: {sorted(zero)}（debug_mode 下 0/1/2 全零是正常的）")

    if a.skip_kms:
        return
    import boto3
    if not a.region:
        raise SystemExit("需要 --region 或者环境变量 AWS_REGION")
    kms = boto3.client("kms", region_name=a.region)
    resp = kms.generate_random(NumberOfBytes=32, Recipient={
        "KeyEncryptionAlgorithm": "RSAES_OAEP_SHA_256", "AttestationDocument": doc})
    if resp.get("Plaintext"):
        raise SystemExit("KMS 返回了明文，说明它没有按 attestation 处理")
    rnd = cms_decrypt(resp["CiphertextForRecipient"], key)
    print(f"KMS GenerateRandom(Recipient) OK：在 Sandbox 里解开了 {len(rnd)} 字节 → KMS 接受这份 attestation 文档")


if __name__ == "__main__":
    main()
