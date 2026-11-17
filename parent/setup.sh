#!/usr/bin/env bash
# 【parent】确认驱动支持 Sandbox，装 fio 和 Firecracker，预留内存池，然后重启
set -euo pipefail
source "$(dirname "$0")/../config.sh"
uname -r                                  # 要 ≥ 6.18.51-120.162.amzn2023
ls /sys/class/misc/nitro_enclaves/        # 必须有 cpu_pool  mem_pool  pcie_slots

sudo dnf install -y -q fio squashfs-tools iptables-nft
ARCH=$(uname -m); TMP=$(mktemp -d)
curl -fsSL https://github.com/firecracker-microvm/firecracker/releases/download/$FC_VERSION/firecracker-$FC_VERSION-$ARCH.tgz | tar -xz -C "$TMP"
sudo install -m 0755 "$TMP/release-$FC_VERSION-$ARCH/firecracker-$FC_VERSION-$ARCH" /usr/local/bin/firecracker
firecracker --version | head -1           # Firecracker v1.18.0

# 内存池：不预留的话 Firecracker 报 "No space left on device (os error 28)"。
# 留得大一点是故意的：page cache 不会用内存池，parent 只剩 ~13 GB 能缓存，读测试不会被缓存抬高
# dirty 门槛改成固定值（重要）：默认按比例算，会把内存池（CMA）的空闲内存也算进去，但块设备的 page cache 用不了内存池。
# 不改的话，大量写入时 dirty 页会占满内存池以外的内存，Sandbox 的写入掉到 ~400 MB/s，多线程写甚至会把 parent 卡死。
printf 'vm.dirty_bytes = 2147483648\nvm.dirty_background_bytes = 536870912\n' | sudo tee /etc/sysctl.d/90-nitro-sandbox.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/90-nitro-sandbox.conf
sudo grubby --update-kernel=ALL --remove-args="nitro_enclaves.mempool"
sudo grubby --update-kernel=ALL --args="nitro_enclaves.mempool=$MEMPOOL"
echo "即将重启，1 分钟后重新登录"
sudo systemctl reboot
