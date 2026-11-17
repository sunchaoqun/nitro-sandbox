#!/usr/bin/env bash
# 【parent】tap0（parent 经它 SSH 进 Sandbox），写配置，启动 Sandbox，把整块 instance store 交给它
set -euo pipefail
source "$(dirname "$0")/../config.sh"
cd "$WORK" && source "$WORK/env"
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
    { "drive_id": "data", "path_on_host": "$ISD", "is_root_device": false, "is_read_only": false,
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
