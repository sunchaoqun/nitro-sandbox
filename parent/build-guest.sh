#!/usr/bin/env bash
# 【parent】准备 Sandbox：内核用 parent 自己的，initrd 用 dracut 生成，rootfs 用 Firecracker CI 的 Ubuntu 24.04 + fio
set -euo pipefail
source "$(dirname "$0")/../config.sh"
cd "$WORK"
K=$(uname -r)
sudo dracut --force --no-hostonly --add-drivers "virtio_pci virtio_blk virtio_net nsm" initrd.img "$K" 2>/dev/null
sudo ls -la initrd.img

S3=https://s3.amazonaws.com/spec.ccfc.min
P=$(curl -fsSL "$S3?list-type=2&prefix=firecracker-ci/&delimiter=/" \
    | grep -oP "(?<=<Prefix>)firecracker-ci/[0-9]{8}-[^/]+/(?=</Prefix>)" | sort | tail -1)
curl -fsSL -o ubuntu.squashfs "$S3/${P}x86_64/ubuntu-24.04.squashfs"
sudo rm -rf rootfs && sudo unsquashfs -q -d rootfs ubuntu.squashfs >/dev/null

rm -f id_rsa id_rsa.pub && ssh-keygen -f id_rsa -N "" -q
sudo mkdir -p rootfs/root/.ssh && sudo cp id_rsa.pub rootfs/root/.ssh/authorized_keys
# CI 镜像精简掉了 apt 要用的目录；resolv.conf 是空的
sudo mkdir -p rootfs/var/tmp rootfs/var/log/apt rootfs/var/cache/apt/archives/partial \
  rootfs/var/lib/apt/lists/partial rootfs/var/cache/debconf rootfs/var/lib/dpkg/updates
sudo chmod 1777 rootfs/tmp rootfs/var/tmp
echo "nameserver 169.254.169.253" | sudo tee rootfs/etc/resolv.conf >/dev/null

for d in proc sys dev dev/pts; do sudo mount --bind /$d rootfs/$d; done
sudo chroot rootfs /usr/bin/env LC_ALL=C DEBIAN_FRONTEND=noninteractive /bin/bash -c \
  'apt-get update -qq && apt-get install -y -qq --no-install-recommends fio >/dev/null && fio --version; apt-get clean'
for d in dev/pts dev sys proc; do sudo umount rootfs/$d; done
echo "rootfs 上残留的挂载：$(mount | grep -c "$WORK/rootfs" || true)"     # 必须是 0

truncate -s 4G rootfs.ext4 && sudo mkfs.ext4 -q -F -d rootfs rootfs.ext4 && ls -la rootfs.ext4
