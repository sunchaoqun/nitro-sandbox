#!/usr/bin/env bash
# 【parent】重启后：确认内存池，找到 instance store 的稳定路径
set -euo pipefail
source "$(dirname "$0")/../config.sh"
grep -o 'nitro_enclaves.mempool=[^ ]*' /proc/cmdline
echo "mem_pool: $(( $(cat /sys/class/misc/nitro_enclaves/mem_pool/total) / 1048576 )) MiB"
echo "pcie_slots: $(cat /sys/class/misc/nitro_enclaves/pcie_slots)"   # ≥ 4
lsblk -d -o NAME,SIZE,MODEL

# NVMe 的编号重启后会变（实测：nvme1n1 一次是 instance store，一次是 EBS 根盘），一律用 by-id 路径
mkdir -p "$WORK"
ISD=/dev/disk/by-id/$(ls /dev/disk/by-id/ | grep -E '^nvme-Amazon_EC2_NVMe_Instance_Storage_[A-Za-z0-9]+$' | head -1)
lsblk -dno MODEL "$(readlink -f "$ISD")" | grep -q "Instance Storage" || { echo "没找到 instance store"; exit 1; }
echo "ISD=$ISD" | tee "$WORK/env"
