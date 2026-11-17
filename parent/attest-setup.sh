#!/usr/bin/env bash
# 【parent】给 rootfs 装上 attestation / KMS / ETH 要用的 Python 依赖和脚本，配好 NAT（Sandbox 要访问 IMDS 和 KMS）
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
source "$REPO/config.sh"
cd "$WORK"
sudo pkill -x firecracker; sleep 1                     # rootfs.ext4 要重建，先停掉 Sandbox
sudo cp "$REPO"/sandbox/*.py rootfs/root/
# CI 镜像的 fcnet-setup.sh 只配 IP、不配默认路由
sudo tee rootfs/etc/systemd/system/fcnet-route.service >/dev/null <<'UNIT'
[Unit]
Description=Default route via the parent's tap
After=fcnet.service
Requires=fcnet.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/ip route replace default via 172.16.0.1 dev eth0

[Install]
WantedBy=multi-user.target
UNIT
sudo ln -sf /etc/systemd/system/fcnet-route.service rootfs/etc/systemd/system/multi-user.target.wants/fcnet-route.service

for d in proc sys dev dev/pts; do sudo mount --bind /$d rootfs/$d; done
sudo chroot rootfs /usr/bin/env LC_ALL=C DEBIAN_FRONTEND=noninteractive /bin/bash -c '
  apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv >/dev/null &&
  python3 -m venv /opt/kms && /opt/kms/bin/pip install -q boto3 cbor2 asn1crypto cryptography eth-account rlp &&
  /opt/kms/bin/python -c "import boto3, cbor2, eth_account; print(\"deps OK\", boto3.__version__, eth_account.__version__)"
  apt-get clean'
for d in dev/pts dev sys proc; do sudo umount rootfs/$d; done
echo "rootfs 上残留的挂载：$(mount | grep -c "$WORK/rootfs")"   # 必须是 0
sudo rm -f rootfs/etc/systemd/system/timers.target.wants/apt-daily*.timer
truncate -s 4G rootfs.ext4 && sudo mkfs.ext4 -q -F -d rootfs rootfs.ext4

HOST_IF=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -qw net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -s 172.16.0.0/30 -o $HOST_IF -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -s 172.16.0.0/30 -o $HOST_IF -j MASQUERADE
sudo iptables -C FORWARD -i tap0 -o $HOST_IF -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 1 -i tap0 -o $HOST_IF -j ACCEPT
sudo iptables -C FORWARD -o tap0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -o tap0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
echo "NAT OK（$HOST_IF）"
