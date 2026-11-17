#!/usr/bin/env bash
# 【parent】可选：去掉 io_engine（默认 Sync），看 Firecracker 默认配置下随机读有多慢。测完用 start-sandbox.sh 恢复
set -uo pipefail
source "$(dirname "$0")/../config.sh"
cd "$WORK"
SB="ssh -n -i $WORK/id_rsa -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@172.16.0.2"
$SB poweroff 2>/dev/null; sleep 5; sudo pkill -x firecracker; sleep 1
sed 's/"io_engine": "Async", //' vm.json > vm-sync.json && grep -c io_engine vm-sync.json   # 0
sudo rm -f /tmp/fc.sock
sudo setsid nohup firecracker --api-sock /tmp/fc.sock --config-file vm-sync.json > fc.log 2>&1 < /dev/null &
for i in $(seq 1 60); do $SB true 2>/dev/null && break; sleep 2; done
mkdir -p results/sandbox-sync
FIO="fio --direct=1 --ioengine=libaio --group_reporting --norandommap --randrepeat=0 --output-format=json --filename=/dev/vdb --time_based --runtime=30 --ramp_time=5 --size=64G"
for t in "2-randread-4k --rw=randread --bs=4k --iodepth=32 --numjobs=4" "5-seqread-1m --rw=read --bs=1M --iodepth=32"; do
  set -- $t; sudo sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  $SB "$FIO --name=$*" > results/sandbox-sync/$1.json
  python3 -c "import json,sys; r=json.load(open(sys.argv[1]))['jobs'][0]['read']; print(sys.argv[2], f'{r[\"iops\"]:,.0f} IOPS', f'{r[\"io_bytes\"]/1e6/(r[\"runtime\"]/1000):,.0f} MB/s')" results/sandbox-sync/$1.json $1
done
