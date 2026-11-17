#!/usr/bin/env bash
# 【parent】把 instance store 格式化成 XFS、挂到 /mnt/instance-store，在上面预分配一个大文件给 Sandbox 当数据盘。可以重复执行。
# 为什么不把裸盘直接交给 Sandbox：Firecracker 不用 O_DIRECT，写入走 parent 的 page cache。裸块设备的 page cache 回写会拆成
# 平均 ~3 KiB 的小请求，被盘的写 IOPS 上限卡住（i7i.2xlarge 上只有 ~425 MB/s）；XFS 文件的回写是 128 KiB 的请求，Sandbox 写入接近直测。
set -euo pipefail
source "$(dirname "$0")/../config.sh"
source "$WORK/env"
MNT=/mnt/instance-store; IMG=$MNT/sandbox-data.img
if ! mountpoint -q $MNT; then
  sudo mkdir -p $MNT
  [ "$(sudo blkid -o value -s TYPE "$ISD" 2>/dev/null)" = xfs ] || sudo mkfs.xfs -q -f "$ISD"   # 盘上不是 XFS 就格式化（数据会清空）
  sudo mount -o noatime "$ISD" $MNT
fi
[ -f $IMG ] || sudo fallocate -l $(( $(df -B1 --output=avail $MNT | tail -1) * 95 / 100 )) $IMG
echo "DATA_IMG=$IMG" > "$WORK/data.env"
echo "数据盘文件：$IMG（$(du -h --apparent-size $IMG | cut -f1)，在 $(findmnt -no SOURCE,FSTYPE $MNT | tr '\n' ' ')上）"
