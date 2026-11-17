#!/usr/bin/env bash
# 【parent】准备数据盘（instance store 上的 XFS 文件）和 tap0，写配置，启动 Sandbox
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)                  # 下面会 cd 到 $WORK，先记下脚本目录
source "$HERE/../config.sh"
cd "$WORK" && source "$WORK/env"
# Firecracker 写盘走 parent 的 page cache；dirty 门槛没改成固定值的话写入会被卡在 ~400 MB/s（见 setup.sh）
[ "$(sysctl -n vm.dirty_bytes)" -gt 0 ] || { echo "vm.dirty_bytes 还是 0：先执行 parent/setup.sh（或者 sudo sysctl -p /etc/sysctl.d/90-nitro-sandbox.conf）"; exit 1; }
bash "$HERE/data-disk.sh" && source "$WORK/data.env"     # 数据盘用 XFS 上的文件，不用裸盘（原因见 data-disk.sh）
ip link show tap0 >/dev/null 2>&1 || sudo ip tuntap add dev tap0 mode tap
sudo ip addr replace 172.16.0.1/30 dev tap0 && sudo ip link set tap0 up

cat > vm.json <<JSON
{
  "boot-source": {
    "kernel_image_path": "/boot/vmlinuz-$(uname -r)",
    "initrd_path": "$WORK/initrd.img",
    "boot_args": "panic=1 initcall_blacklist=i8042_init net.ifnames=0 rd.driver.pre=virtio_pci,virtio_blk,nsm"
  },
  "machine-config": { "vcpu_count": 4, "mem_size_mib": 4096 },
  "drives": [
    { "drive_id": "rootfs", "path_on_host": "$WORK/rootfs.ext4", "is_root_device": true, "is_read_only": false },
    { "drive_id": "data", "path_on_host": "$DATA_IMG", "is_root_device": false, "is_read_only": false,
      "io_engine": "Async", "cache_type": "Writeback" }
  ],
  "network-interfaces": [ { "iface_id": "eth0", "guest_mac": "06:00:AC:10:00:02", "host_dev_name": "tap0" } ],
  "enclave": { "cpu_overcommit": false }
}
JSON
sudo pkill -x firecracker 2>/dev/null && sleep 1 || true
sudo rm -f /tmp/fc.sock
sudo setsid nohup firecracker --api-sock /tmp/fc.sock --config-file vm.json > fc.log 2>&1 < /dev/null &
SB=(ssh -n -i "$WORK/id_rsa" -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@172.16.0.2)
for i in $(seq 1 60); do "${SB[@]}" true 2>/dev/null && break; sleep 2; done
grep -E "Attaching enclave block|debug=" fc.log | sed 's/.*main\] //'
"${SB[@]}" 'echo "vCPU: $(nproc)"; lsblk -d -o NAME,SIZE /dev/vdb; ls -la /dev/nsm'
