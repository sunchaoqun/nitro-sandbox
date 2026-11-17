#!/usr/bin/env python3
"""把实测日志脱敏后存成 docs/expected/*.txt，fio 结果提取成 docs/data/results.json。
用法（维护者用）：python3 tools/sanitize.py <日志目录> <本轮 fio 结果目录> [上一轮 fio 结果目录]"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS, RUN = sys.argv[1], sys.argv[2]
PREV = sys.argv[3] if len(sys.argv) > 3 else None

# 账号、实例、key、IP、盘序列号都换成 AWS 文档里的示例值
REPLACE = json.load(open(os.environ.get("SANITIZE_MAP", os.path.join(LOGS, "..", "sanitize-map.json"))))   # {"真实值": "示例值"}，不提交
REGEX = [(r"enc[0-9a-f]{16}\b", "enc0123456789abcdef")]


def clean(text):
    for real, fake in REPLACE.items():
        text = text.replace(real, fake)
    for pat, rep in REGEX:
        text = re.sub(pat, rep, text)
    lines = [l for l in text.split("\n") if not re.match(r"^(real|user|sys)\s+\d", l)]
    return "\n".join(lines).strip("\n") + "\n"


os.makedirs(os.path.join(REPO, "docs", "expected"), exist_ok=True)
for name in sorted(os.listdir(LOGS)):
    if name.endswith(".txt"):
        out = clean(open(os.path.join(LOGS, name)).read())
        open(os.path.join(REPO, "docs", "expected", name), "w").write(out)
leaks = [name for name in os.listdir(os.path.join(REPO, "docs", "expected"))
         if any(real in open(os.path.join(REPO, "docs", "expected", name)).read() for real in REPLACE)]
assert not leaks, f"还有没脱敏的：{leaks}"


def job(path):
    raw = open(path).read()
    return json.loads(raw[raw.index("{"):])["jobs"][0]


def metric(path, kind):
    j = job(path)
    s = j["read"] if j["read"]["io_bytes"] else j["write"]
    if kind == "MB/s":
        return round(s["io_bytes"] / 1e6 / (j["job_runtime"] / 1000))
    if kind == "IOPS":
        return round(s["iops"])
    return [round(s["clat_ns"]["mean"] / 1000), round(s["clat_ns"]["percentile"]["99.000000"] / 1000)]


TESTS = [("6-bigfile-64g", "MB/s"), ("1-seqwrite-64g", "MB/s"), ("5-seqread-1m", "MB/s"),
         ("2-randread-4k", "IOPS"), ("4-randwrite-4k", "IOPS"), ("3-randread-4k-qd1", "µs")]
data = {"run": {}, "prev": {}, "sync": {}}
for key, unit in TESTS:
    data["run"][key] = {m: metric(f"{RUN}/{m}/{key}.json", unit) for m in ("local", "sandbox")}
    if PREV and os.path.exists(f"{PREV}/local/{key}.json"):
        data["prev"][key] = {m: metric(f"{PREV}/{m}/{key}.json", unit) for m in ("local", "sandbox")}
data["sync"] = {"randread": metric(f"{RUN}/sandbox-sync/2-randread-4k.json", "IOPS"),
                "seqread": metric(f"{RUN}/sandbox-sync/5-seqread-1m.json", "MB/s")}
json.dump(data, open(os.path.join(REPO, "docs", "data", "results.json"), "w"), ensure_ascii=False, indent=1)
print("expected:", len(os.listdir(os.path.join(REPO, "docs", "expected"))), "个文件；results.json 已写好")
