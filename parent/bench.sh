#!/usr/bin/env bash
# 【parent】同一套 fio 测试，分别跑在 parent 直测和 Sandbox 里。会清空 instance store 上的数据！
#   bash parent/bench.sh local     直接测 parent 上的 instance store（Sandbox 不要开着）
#   bash parent/bench.sh sandbox   测 Sandbox 里的 /dev/vdb（就是同一块 instance store）
# 结果：$WORK/results/<local|sandbox>/*.json
set -euo pipefail
source "$(dirname "$0")/../config.sh"
cd "$WORK" && source "$WORK/env"
MODE=${1:?用法: bash parent/bench.sh local|sandbox}
OUT=$WORK/results/$MODE; mkdir -p "$OUT"
SB="ssh -n -i $WORK/id_rsa -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@172.16.0.2"
case $MODE in
  local)   DEV=$ISD;     X() { sudo "$@"; }
           pgrep -x firecracker >/dev/null && { echo "先停掉 Sandbox：sudo pkill -x firecracker"; exit 1; } ;;
  sandbox) DEV=/dev/vdb; X() { $SB "$(printf '%q ' "$@")"; } ;;
  *) echo "用法: bash parent/bench.sh local|sandbox"; exit 1 ;;
esac
FIO=(fio --direct=1 --ioengine=libaio --group_reporting --norandommap --randrepeat=0 --output-format=json)
T=(--time_based --runtime=30 --ramp_time=5 --size=64G)          # 都在盘的前 64 GiB 里测
run() {   # 每项之前清一次 parent 的 page cache（Firecracker 的读写会经过它）
  local name=$1; shift
  sudo sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  echo "$(date +%T) $MODE: $name"
  X "${FIO[@]}" --name=$name "$@" > "$OUT/$name.json"
}
run 1-seqwrite-64g    --filename=$DEV --rw=write --bs=1M --iodepth=32 --size=64G --end_fsync=1   # 先写满测试区（没写过的地方读起来特别快）
run 2-randread-4k     --filename=$DEV --rw=randread --bs=4k --iodepth=32 --numjobs=4 "${T[@]}"
run 3-randread-4k-qd1 --filename=$DEV --rw=randread --bs=4k --iodepth=1 "${T[@]}"
run 4-randwrite-4k    --filename=$DEV --rw=randwrite --bs=4k --iodepth=32 --numjobs=4 --end_fsync=1 "${T[@]}"
run 5-seqread-1m      --filename=$DEV --rw=read --bs=1M --iodepth=32 "${T[@]}"
# 大文件：在 ext4 上写一个 64 GiB 的文件（largefile + 关掉 lazy init，避免后台初始化干扰）
X mkfs.ext4 -q -F -T largefile -E nodiscard,lazy_itable_init=0,lazy_journal_init=0 $DEV
X mkdir -p /mnt/bench; X mount $DEV /mnt/bench
run 6-bigfile-64g     --filename=/mnt/bench/big.dat --rw=write --bs=1M --iodepth=32 --size=64G --end_fsync=1
X umount /mnt/bench
echo "$(date +%T) $MODE: 完成 → $OUT"
