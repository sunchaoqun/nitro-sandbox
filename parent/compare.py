#!/usr/bin/env python3
"""【parent】对比 parent 直测和 Sandbox：python3 parent/compare.py（读 $WORK/results，默认 ~/bench/results）"""
import json, os
R = os.path.join(os.environ.get("WORK", os.path.expanduser("~/bench")), "results")
TESTS = [  # (文件, 显示名, 指标)
    ("1-seqwrite-64g", "大块顺序写 64 GiB（直接写块设备，1M QD32，含 fsync）", "MB/s"),
    ("6-bigfile-64g", "大文件写入 64 GiB（ext4 文件，1M QD32，含 fsync）", "MB/s"),
    ("5-seqread-1m", "顺序读（1M QD32）", "MB/s"),
    ("2-randread-4k", "4K 随机读（QD32×4）", "IOPS"),
    ("4-randwrite-4k", "4K 随机写（QD32×4，含 fsync）", "IOPS"),
    ("3-randread-4k-qd1", "4K 随机读延迟（QD1，平均 / p99）", "µs"),
]
def load(mode, name):
    raw = open(f"{R}/{mode}/{name}.json").read()
    return json.loads(raw[raw.index("{"):])["jobs"][0]
def value(j, unit):
    s = j["read"] if j["read"]["io_bytes"] else j["write"]
    if unit == "MB/s":   # 按 job_runtime 算（包含最后的 fsync）
        return s["io_bytes"] / 1e6 / (j["job_runtime"] / 1000), None
    if unit == "IOPS":
        return s["iops"], None
    return s["clat_ns"]["mean"] / 1000, s["clat_ns"]["percentile"]["99.000000"] / 1000
print(f"{'测试':<44}{'parent 直测':>16}{'Sandbox':>16}{'Sandbox/直测':>14}")
for name, label, unit in TESTS:
    (a, ap), (b, bp) = value(load("local", name), unit), value(load("sandbox", name), unit)
    if unit == "µs":
        print(f"{label:<40}{f'{a:.0f} / {ap:.0f} µs':>22}{f'{b:.0f} / {bp:.0f} µs':>18}{f'+{b - a:.0f} µs':>14}")
    else:
        print(f"{label:<40}{f'{a:,.0f} {unit}':>22}{f'{b:,.0f} {unit}':>18}{f'{100 * b / a:.0f}%':>14}")
