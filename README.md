# EC2 Nitro Sandbox Workshop：attestation、ETH 签名、instance store 性能

在一台 EC2 上从零搭起 Nitro Sandbox（Firecracker），实测三件事：

1. **KMS 能不能只对 PCR 匹配的 Sandbox 放行**：带 attestation 能解密，不带 attestation、parent、封口之后、改过镜像、debug 模式都被拒
2. **以太坊交易怎么签、谁能签**：KMS Sign（私钥不出 KMS）和 attestation 保护的钱包（私钥只在 Sandbox 内存里），外加 Sepolia 上真转一笔
3. **把整块 instance store 交给 Sandbox 后读写有多快**：和 parent 上直接测对比

每一步执行的都是仓库里的脚本，每一步都附了实测的预期输出（2026-11-17，在全新的账号资源上从第 1 步跑到第 28 步）。图文版（同样的步骤，带结果矩阵和性能图表）：https://sunchaoqun.github.io/nitro-sandbox/

> **更正（2026-11-17）**：早先版本把 instance store 的裸盘直接交给 Sandbox，测出写入只有直测的 25%（i7i.2xlarge 约 425 MB/s），并把它说成 Sandbox 的限制，这是错的。根因是 parent 回写裸块设备的 page cache 时会拆成平均约 3 KiB 的小请求，被盘的写 IOPS 上限卡住：parent 自己这样写也只有 424 MB/s。现在 start-sandbox.sh 用 instance store 上 XFS 里的一个文件当数据盘，回写是 128 KiB 的请求，Sandbox 顺序写达到直测的 100%（i7i.2xlarge）和 99%（i7i.8xlarge）。

## 结果一览

| 谁来要 | 加密的数据 | ETH 钱包私钥 | KMS Sign 签 ETH |
|---|---|---|---|
| Sandbox，原镜像，带 attestation | ✅ 能解开（第 16 步） | ✅ 能创建、能签名（第 18 步） | ✅ 能签（第 20 步） |
| Sandbox，不带 attestation | ❌ 拒绝（第 17 步） | ❌ 拒绝（第 19 步） | – |
| parent，用同一个实例角色 | ❌ 拒绝（第 17 步） | ❌ 拒绝（第 19 步） | ⚠️ 也能签（第 21 步） |
| 封口之后（PCR16 已锁定） | ❌ 拒绝（第 22 步） | ❌ 拒绝（第 22 步） | – |
| 改过镜像（cmdline 多一个 quiet） | ❌ 拒绝（第 23 步） | ❌ 拒绝（第 23 步） | – |
| debug 模式（PCR0～2 全零） | ❌ 拒绝（第 24 步） | 没测 | – |
| 恢复原镜像重启 | ✅ 能解开（第 25 步） | ✅ 能签名（第 25 步） | – |

KMS Sign 那一列只测了前三行：kms:Sign 不支持 attestation，key policy 只能按 IAM 身份放行，所以 parent 也能签。

| instance store（i7i.2xlarge） | parent 直测 | Sandbox | Sandbox / 直测 |
|---|---|---|---|
| 大文件写入 | 1,706 MB/s | 1,719 MB/s | 101% |
| 大块顺序写 | 1,719 MB/s | 1,713 MB/s | 100% |
| 顺序读 | 2,134 MB/s | 2,142 MB/s | 100% |
| 4K 随机读 | 300,467 IOPS | 244,562 IOPS | 81% |
| 4K 随机写 | 165,119 IOPS | 164,877 IOPS | 100% |
| 4K 随机读延迟（平均 / p99） | 74 / 84 µs | 98 / 119 µs | +23 µs |
| 4K 随机读，默认 Sync 引擎 | – | 12,737 IOPS | Async 的 1/19 |

上面是按本手册步骤的一次完整运行。另外单独复测过：数据盘用 XFS 文件时 Sandbox 顺序写 i7i.2xlarge 1,701 MB/s、i7i.8xlarge 3,529 MB/s，和这里的结果一致。

### 数据盘为什么要用 XFS 上的文件，不能用裸盘

Firecracker 打开数据盘时不用 O_DIRECT，Sandbox 的写入先进 parent 的 page cache，再由 parent 内核回写。回写裸块设备时请求被拆得很小，被盘的写 IOPS 上限卡住；回写 XFS 上的文件是 128 KiB 的请求，和 O_DIRECT 一样快。

| 机型 | 顺序写 64 GiB（1 MiB 块，含 fsync） | 写入 MB/s | 盘上写请求/秒 | 平均每个请求 |
|---|---|---|---|---|
| i7i.2xlarge | 裸盘 O_DIRECT（QD32） | 1,733 | 13,003 | 128 KiB |
| i7i.2xlarge | 裸盘 带缓存写 | 425 | 163,214 | 3 KiB |
| i7i.2xlarge | XFS 文件 带缓存写 | 1,709 | 12,841 | 128 KiB |
| i7i.2xlarge | Sandbox，数据盘是 XFS 上的文件 | 1,701 | 12,647 | 128 KiB |
| i7i.8xlarge | 裸盘 O_DIRECT（QD32） | 3,622 | 26,597 | 128 KiB |
| i7i.8xlarge | 裸盘 带缓存写 | 1,927 | 197,362 | 9 KiB |
| i7i.8xlarge | XFS 文件 带缓存写 | 3,539 | 26,057 | 128 KiB |
| i7i.8xlarge | Sandbox，数据盘是 XFS 上的文件 | 3,529 | 25,375 | 128 KiB |

用裸盘时还排除过这些因素（i7i.2xlarge，Sandbox 顺序写）：dirty 默认 419 / 421、dirty 固定 425 / 425、关掉写回限流 425、Sandbox 只给 2 vCPU 425 MB/s，parent 的 CPU 一直很闲。盘上请求数是测试期间 /sys/block/<盘>/stat 的增量。

### i7i.8xlarge、多盘 RAID 0

数据盘同样用 XFS 文件：单盘 Sandbox 顺序写是直测的 99%；两块盘各放一个文件、在 Sandbox 里组 RAID 0，顺序写 7,528 MB/s，和 parent 直测两盘 RAID 0（7,572）几乎一样。随机读是例外：每个 Sandbox 的 4K 随机读只有 25～31 万 IOPS，大机型上明显低于直测。

| i7i.8xlarge | 顺序写 MB/s | 顺序读 MB/s | 4K 随机读 IOPS | 4K 随机写 IOPS |
|---|---|---|---|---|
| parent 直测：单盘 | 3,612 | 3,640 | 593,778 | 330,676 |
| Sandbox：单盘（XFS 文件） | 3,592 | 4,098 | 251,338 | 302,195 |
| parent 直测：两盘 RAID 0 | 7,572 | 8,533 | 950,264 | 661,273 |
| Sandbox：两块盘各一个 XFS 文件，Sandbox 里 RAID 0 | 7,528 | 5,087 | 310,818 | 377,713 |

单位：MB/s、IOPS。Sandbox 4 vCPU，fio 参数和 A 部分一样（direct=1，64 GiB 测试区，写入含 fsync）；单盘两行来自按本手册步骤的一次完整运行，RAID 0 两行不在 workshop 步骤里。单盘大文件写入：直测 3,631、Sandbox 3,558 MB/s。单盘直测顺序读波动较大（另一次测试是 4,267 MB/s），所以这一行 Sandbox 比直测高不代表读得更快。早先把裸盘直接交给 Sandbox 时：单盘顺序写 dirty 默认 491 / 555、dirty 固定 1,876～2,042 MB/s；在 parent 上做 RAID 0 再当一块盘给 Sandbox，顺序读只有 1,008 MB/s，不要这么做。

## 开始之前

- **工作机**（你自己的电脑或者一台跳板机）：AWS CLI v2 并配好有管理员权限的凭证、git、python3、ssh
- **区域**：默认 us-west-1；换区域就改 workshop.env 里的 AWS_REGION，并确认该区域、该子网的可用区有 i7i.2xlarge
- **网络**：一个能从工作机 ssh 进去的子网（有公网 IP，或者工作机就在同一个 VPC），安全组放行 22 端口；parent 要能访问公网（下载 Firecracker、Ubuntu 软件包、以太坊 RPC）
- **费用**：i7i.2xlarge 按需 $0.83/小时（us-west-1），全程约 1.5 小时；两把 KMS key 各 $1/月（清理后 7 天删除）；Sepolia 测试币免费
- **会创建的资源**：实例角色 `nitro-sandbox-workshop`、KMS key `alias/nitro-sandbox-workshop-eth` 和 `alias/nitro-sandbox-workshop-attest`、一台 parent 实例。名字都在 config.sh 里，可以在 workshop.env 里改
- **注意**：bench.sh 会清空 instance store；ETH 部分只用 Sepolia 测试网

## 仓库结构

```
config.sh              所有脚本共用的配置（区域、机型、角色名、key 别名）
workshop.env.example   复制成 workshop.env，填子网、安全组、key pair
workstation/           在工作机上执行：prereq / launch-parent / create-attest-key / cloudtrail / cleanup
parent/                在 parent 上执行：setup / after-reboot / build-guest / bench / start-sandbox / attest-setup / attest …
sandbox/               装进 Sandbox 的 Python：NSM attestation、KMS 加解密、attestation 钱包、KMS Sign 签 ETH
docs/                  图文版手册（index.html）、每一步的预期输出（expected/）、性能数据（data/）
tools/                 生成 README 和 docs 的脚本（维护者用）
```

## 准备

### 第 1 步：clone 仓库，填好 workshop.env（在工作机上）

工作机要装好 AWS CLI v2（已配置好管理员凭证）、git、python3。workshop.env 里填 parent 用的子网、安全组和 EC2 key pair；安全组要放行你电脑的 22 端口。workshop.env 在 .gitignore 里，不会被提交。

```bash
git clone https://github.com/sunchaoqun/nitro-sandbox.git
cd nitro-sandbox
cp workshop.env.example workshop.env
vi workshop.env        # 改 AWS_REGION、SUBNET_ID、SG_ID、KEY_NAME、PEM
```

脚本：[workshop.env.example](workshop.env.example)、[config.sh](config.sh)

### 第 2 步：创建实例角色和 ETH 签名 key（在工作机上）

ETH key 是 KMS 里的 secp256k1 密钥，私钥不出 KMS。实例角色只能对它做 Sign/GetPublicKey，外加 GenerateRandom（NSM 探针用）。已经有了会跳过，可以重复执行。

```bash
bash workstation/prereq.sh
```

脚本：[workstation/prereq.sh](workstation/prereq.sh)

<details><summary>预期输出</summary>

```text
账号 111122223333，区域 us-west-1
ETH key 已创建：alias/nitro-sandbox-workshop-eth（abcd1234-ab12-cd34-ef56-abcdef123456）
arn:aws:iam::111122223333:role/nitro-sandbox-workshop
instance profile 已创建，等 15 秒让它生效
角色策略：kms-eth-sign	kms-generate-random
完成

# 再执行一次：
账号 111122223333，区域 us-west-1
ETH key 已存在：alias/nitro-sandbox-workshop-eth
角色已存在：nitro-sandbox-workshop
角色策略：kms-eth-sign	kms-generate-random
完成
```
</details>

### 第 3 步：开一台带 instance store 的 parent（在工作机上）

默认 i7i.2xlarge：自带一块 1875 GB 的 NVMe instance store，支持 Nitro Enclave（Sandbox 走同一个开关）。AMI 用最新的 AL2023 kernel-6.18，它的驱动才支持 Sandbox。实例 ID 会记在仓库根目录的 .parent 里，清理时要用。

```bash
bash workstation/launch-parent.sh
```

脚本：[workstation/launch-parent.sh](workstation/launch-parent.sh)

<details><summary>预期输出</summary>

```text
IID=i-0123456789abcdef0
PUBLIC_IP=203.0.113.25
PRIVATE_IP=10.0.1.25
大约 1 分钟后可以登录：
  从公网：ssh -i ~/.ssh/my-key.pem ec2-user@203.0.113.25
  从同一个 VPC 里：ssh -i ~/.ssh/my-key.pem ec2-user@10.0.1.25
```
</details>

### 第 4 步：登录 parent，clone 仓库（在parent上）

用上一步打印的 ssh 命令登录。parent 上也要一份仓库，后面的脚本都从这里执行。

```bash
sudo dnf install -y -q git
git clone https://github.com/sunchaoqun/nitro-sandbox.git
cd nitro-sandbox
```

<details><summary>预期输出</summary>

```text
Cloning into 'nitro-sandbox'...
```
</details>

### 第 5 步：装 fio 和 Firecracker，预留内存池（会重启）（在parent上）

Sandbox 的内存来自驱动预留的连续内存池，不预留就起不来。脚本还会把 dirty 门槛改成固定值（vm.dirty_bytes=2G / vm.dirty_background_bytes=512M），大内存池时默认按比例的门槛会让 parent 在大量写入时卡死（见常见问题）。最后会重启 parent，1 分钟后重新登录，并 cd ~/nitro-sandbox。

```bash
bash parent/setup.sh
```

脚本：[parent/setup.sh](parent/setup.sh)

<details><summary>预期输出</summary>

```text
6.18.62-127.179.amzn2023.x86_64
cpu_pool
dev
device_pool
mem_pool
pcie_slots
pcie_slots_in_use
pcie_slots_total
power
subsystem
uevent

Installed:
  fio-3.32-2.amzn2023.0.3.x86_64                                                
  fio-engine-http-3.32-2.amzn2023.0.3.x86_64                                    
  fio-engine-libaio-3.32-2.amzn2023.0.3.x86_64                                  
  fio-engine-rdma-3.32-2.amzn2023.0.3.x86_64                                    
  iptables-libs-1.8.8-3.amzn2023.0.2.x86_64                                     
  iptables-nft-1.8.8-3.amzn2023.0.2.x86_64                                      
  libnetfilter_conntrack-1.0.8-2.amzn2023.0.2.x86_64                            
  libnfnetlink-1.0.1-19.amzn2023.0.2.x86_64                                     
  libnftnl-1.2.2-2.amzn2023.0.2.x86_64                                          
  librdmacm-48.0-1.amzn2023.0.1.x86_64                                          
  lzo-2.10-4.amzn2023.0.2.x86_64                                                
  squashfs-tools-4.5-3.amzn2023.0.2.x86_64                                      

Firecracker v1.18.0
即将重启，1 分钟后重新登录
```
</details>

### 第 6 步：确认内存池，记下 instance store 的稳定路径（在parent上）

NVMe 的编号每次开机都可能变：实测重启后 nvme1n1 一次是 instance store，一次是 EBS 根盘。所以后面一律用按序列号的 /dev/disk/by-id 路径，脚本会确认型号是 Instance Storage。

```bash
cd ~/nitro-sandbox
bash parent/after-reboot.sh
```

脚本：[parent/after-reboot.sh](parent/after-reboot.sh)

<details><summary>预期输出</summary>

```text
nitro_enclaves.mempool=48G
mem_pool: 49152 MiB
pcie_slots: 4
vm.dirty_bytes = 2147483648
vm.dirty_background_bytes = 536870912
NAME     SIZE MODEL
nvme0n1   30G Amazon Elastic Block Store
nvme1n1  1.7T Amazon EC2 NVMe Instance Storage
ISD=/dev/disk/by-id/nvme-Amazon_EC2_NVMe_Instance_Storage_AWS1234567890ABCDEF0
```
</details>

### 第 7 步：准备 Sandbox 的内核、initrd 和 rootfs（在parent上）

内核直接用 parent 自己的 /boot/vmlinuz，dracut 打一个带 virtio 和 nsm 驱动的 initrd；rootfs 是 Firecracker CI 的 Ubuntu 24.04，chroot 进去装好 fio。大约 30 秒，debconf 的提示可以忽略。

```bash
bash parent/build-guest.sh
```

脚本：[parent/build-guest.sh](parent/build-guest.sh)

<details><summary>预期输出</summary>

```text
-rw-------. 1 root root 23570747 Nov 17 06:10 initrd.img
debconf: delaying package configuration, since apt-utils is not installed
fio-3.36
rootfs 上残留的挂载：0
-rw-rw-r--. 1 ec2-user ec2-user 4294967296 Nov 17 06:11 rootfs.ext4
```
</details>

## A. instance store 性能

### 第 8 步：测 parent 直测（这时 Sandbox 还没开）（在parent上）

直接读写 instance store 裸盘（O_DIRECT），作为对比基线。会清空盘上的数据（包括 XFS，之后 start-sandbox.sh 会重新格式化）。大约 4 分钟。

```bash
bash parent/bench.sh local
```

脚本：[parent/bench.sh](parent/bench.sh)

<details><summary>预期输出</summary>

```text
10:02:45 local: 1-seqwrite-64g
10:03:25 local: 2-randread-4k
10:04:01 local: 3-randread-4k-qd1
10:04:36 local: 4-randwrite-4k
10:05:12 local: 5-seqread-1m
10:05:48 local: 6-bigfile-64g
10:06:29 local: 完成 → /home/ec2-user/bench/results/local
```
</details>

### 第 9 步：启动 Sandbox，数据盘是 instance store 上 XFS 里的文件（在parent上）

data-disk.sh 把 instance store 格式化成 XFS（第一次）、挂到 /mnt/instance-store，预分配一个大文件给 Sandbox 当数据盘。不要把裸盘直接交给 Sandbox：parent 回写裸块设备的缓存时会拆成约 3 KiB 的小请求，写入只有直测的 1/4（见 A 的结果）。Sandbox 是 confidential 模式（非 debug），4 个独占 vCPU，4 GiB 内存；数据盘 io_engine 设成 Async，cache_type 设成 Writeback（让 Sandbox 里的 fsync 真正落盘）。

```bash
bash parent/start-sandbox.sh
```

脚本：[parent/start-sandbox.sh](parent/start-sandbox.sh)、[parent/data-disk.sh](parent/data-disk.sh)

<details><summary>预期输出</summary>

```text
数据盘文件：/mnt/instance-store/sandbox-data.img（1.7T，在 /dev/nvme1n1 xfs 上）
Attaching enclave block device id=rootfs
Attaching enclave block device id=data
Starting enclave with start_flags=0x0 (debug=false)
vCPU: 4
NAME  SIZE
vdb   1.6T
crw-rw-rw- 1 root root 10, 258 Nov 17 10:06 /dev/nsm
```
</details>

### 第 10 步：测 Sandbox（在parent上）

同一块盘、同样的 fio 参数，这次在 Sandbox 里对 /dev/vdb 跑。大约 5 分钟。

```bash
bash parent/bench.sh sandbox
```

<details><summary>预期输出</summary>

```text
10:06:39 sandbox: 1-seqwrite-64g
10:07:20 sandbox: 2-randread-4k
10:07:59 sandbox: 3-randread-4k-qd1
10:08:35 sandbox: 4-randwrite-4k
10:09:15 sandbox: 5-seqread-1m
10:09:55 sandbox: 6-bigfile-64g
10:10:35 sandbox: 完成 → /home/ec2-user/bench/results/sandbox
```
</details>

### 第 11 步：对比结果（在parent上）

i7i.2xlarge 上：顺序读写和 4K 随机写应该和直测差不多（95～101%），4K 随机读在 80% 左右。

```bash
python3 parent/compare.py
```

脚本：[parent/compare.py](parent/compare.py)

<details><summary>预期输出</summary>

```text
测试                                                 parent 直测         Sandbox    Sandbox/直测
大块顺序写 64 GiB（直接写块设备，1M QD32，含 fsync）                1,719 MB/s        1,713 MB/s          100%
大文件写入 64 GiB（ext4 文件，1M QD32，含 fsync）               1,706 MB/s        1,719 MB/s          101%
顺序读（1M QD32）                                        2,134 MB/s        2,142 MB/s          100%
4K 随机读（QD32×4）                                    300,467 IOPS      244,562 IOPS           81%
4K 随机写（QD32×4，含 fsync）                            165,119 IOPS      164,877 IOPS          100%
4K 随机读延迟（QD1，平均 / p99）                              74 / 84 µs       98 / 119 µs        +23 µs
```
</details>

### 第 12 步：（可选）看看默认的 Sync 引擎有多慢（在parent上）

把 io_engine 去掉再测一次随机读和顺序读。这一步说明了为什么启动 Sandbox 那一步一定要写 Async。

```bash
bash parent/sync-test.sh
```

脚本：[parent/sync-test.sh](parent/sync-test.sh)

<details><summary>预期输出</summary>

```text
0
2-randread-4k 12,737 IOPS 52 MB/s
5-seqread-1m 853 IOPS 896 MB/s
```
</details>

## B. Attestation 和 ETH 签名

### 第 13 步：给 rootfs 装 Python 依赖和脚本，配好 NAT（在parent上）

把 sandbox/ 下的 4 个 Python 脚本装进 Sandbox 的 rootfs；Sandbox 要经 parent 的 NAT 访问 IMDS（实例角色的凭证）、KMS 和以太坊 RPC。rootfs 不在 PCR 的度量范围内，所以装依赖不影响 PCR0。大约 30 秒。

```bash
bash parent/attest-setup.sh
```

脚本：[parent/attest-setup.sh](parent/attest-setup.sh)、[parent/attest.sh](parent/attest.sh)、[sandbox/nitro_attest.py](sandbox/nitro_attest.py)、[sandbox/kms_attest_poc.py](sandbox/kms_attest_poc.py)、[sandbox/kms_attest_wallet.py](sandbox/kms_attest_wallet.py)、[sandbox/kms_sign_tx.py](sandbox/kms_sign_tx.py)

<details><summary>预期输出</summary>

```text
deps OK 1.44.4 0.14.0
rootfs 上残留的挂载：0
NAT OK（enp39s0）
```
</details>

### 第 14 步：启动 Sandbox，跑 NSM 探针，记下 PCR0（在parent上）

Sandbox 跟 /dev/nsm 要一份 attestation 文档，打印里面的 PCR，再用它调一次 KMS GenerateRandom(Recipient)。能跑通说明 KMS 接受这台 Sandbox 的 attestation。复制 PCR0 那一行的 96 个字符，下一步要用。

```bash
bash parent/attest.sh restart normal
bash parent/attest.sh probe
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh restart normal
Sandbox 已启动（normal）
$ bash parent/attest.sh probe
attestation 文档: 4758 字节, module_id=i-0123456789abcdef0-enc0123456789abcdef, digest=SHA384
  PCR0  22e450ea930cfbcc6933363f99bf5dd788605c6c2e61d1f4731aaed3359929b64144b22866c5367a5a299d016e10fbad
  PCR1  67d3e9e081dbc966527ce5feb2b6fb2f195838da4223436ab4af0c3aad61edf3f35fad00d7d71ee5805b39ad85f08e29
  PCR2  21b9efbc184807662e966d34f390821309eeac6802309798826296bf3e8bec7c10edb30948c90ba67310f7b964fc500a
  PCR3  9f81fb901a162c0df7b1652eb155e6dba797a46a0cfaf0e9c20ae669734f4afa17a23152a203fc1d6911e3b12c6674c4
  PCR4  b6a321937c4fc1d04ef0823e880c7db5c91d36c36f4de05107ff667474c36453c5314d8030bd2e8d133990b26421c351
  PCR5  da45a8190e1ae67614682c79a14783e79ad0f3fad47a2017be8fe17c5c1147166dd8edcd0d5a07304fa0dabf55291bd5
  PCR6  0ca1f41732f0a18892f8591481cce252b2d2817ad3fa8bd2bea89b59c8cbf9064b055f8f19b3bc09840d1763cb00e772
  全零的 PCR: [7, 8, 9, 10, 11, 12, 13, 14, 15]（debug_mode 下 0/1/2 全零是正常的）
KMS GenerateRandom(Recipient) OK：在 Sandbox 里解开了 32 字节 → KMS 接受这份 attestation 文档
```
</details>

### 第 15 步：创建只认这个 PCR0 的 KMS key（在工作机上）

key policy 五条：管理员能管理但不能解密；谁都能加密；只有 PCR0 匹配的 Sandbox 能解密和生成钱包；PCR0 不匹配（包括根本没带 attestation）一律拒绝；文档里出现 PCR16（封口）一律拒绝。别名已经存在时只更新 key policy。

```bash
bash workstation/create-attest-key.sh <上一步的 PCR0>
```

脚本：[workstation/create-attest-key.sh](workstation/create-attest-key.sh)

<details><summary>预期输出</summary>

```text
Access Analyzer：策略没有问题
已创建 alias/nitro-sandbox-workshop-attest（1234abcd-12ab-34cd-56ef-1234567890ab），只认 PCR0=22e450ea930cfbcc…

# 再执行一次（别名已存在，只更新 key policy）：
Access Analyzer：策略没有问题
已更新 alias/nitro-sandbox-workshop-attest 的 key policy（PCR0=22e450ea930cfbcc…）
```
</details>

### 第 16 步：加密，再带 attestation 解密（在parent上）

加密不需要 attestation；解密时 Sandbox 把 attestation 文档（带一把临时 RSA 公钥）交给 KMS，KMS 核对 PCR0 后只把明文加密给那把公钥，所以明文只在 Sandbox 里出现。

```bash
bash parent/attest.sh encrypt "hello nitro sandbox"
bash parent/attest.sh decrypt
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh encrypt "hello nitro sandbox"
已加密 → secret.b64（KeyId: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab）
$ bash parent/attest.sh decrypt
本次 attestation 的 PCR0 : 22e450ea930cfbcc6933363f99bf5dd788605c6c2e61d1f4731aaed3359929b64144b22866c5367a5a299d016e10fbad
本次 attestation 的 PCR16: 不在文档里（未封口）
KeyId   : arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab
解密结果: hello nitro sandbox
```
</details>

### 第 17 步：负面测试：不带 attestation、parent 直接解密（在parent上）

同一个实例角色、同一段密文：Sandbox 里不带 attestation 解密，以及 parent 自己调 KMS 解密，都必须被拒（explicit deny）。

```bash
bash parent/attest.sh decrypt-noattest
bash parent/attest.sh parent-decrypt
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh decrypt-noattest
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
$ bash parent/attest.sh parent-decrypt
aws: [ERROR]: An error occurred (AccessDeniedException) when calling the Decrypt operation: User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
```
</details>

### 第 18 步：attestation 保护的 ETH 钱包：创建并签名（在parent上）

KMS GenerateDataKeyPair(ECC_SECG_P256K1) 只把私钥加密给 attestation 里的公钥；之后每次签名先带 attestation 解密，在 Sandbox 内存里签 EIP-1559 交易，再用 eth-account 独立验证发送方。

```bash
bash parent/attest.sh wallet-create
bash parent/attest.sh wallet-sign
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh wallet-create
本次 attestation 的 PCR0: 22e450ea930cfbcc… 
钱包地址      : 0x6B43959455acf40c93561D5fb51876b31e671933
已保存 wallet.json：只有地址、公钥和加密的私钥，明文私钥没有写盘
$ bash parent/attest.sh wallet-sign
本次 attestation 的 PCR0: 22e450ea930cfbcc… 
钱包地址      : 0x6B43959455acf40c93561D5fb51876b31e671933
交易哈希      : 0xcaddd9c9a603fb1898103ab4a729c7f75904904d3dc9f2ef8885272817e8c315
原始交易      : 0x02f87583aa36a780843b9aca008506fc23ac008252089400000000000000000000…
eth-account 验证发送方: 0x6B43959455acf40c93561D5fb51876b31e671933 OK
```
</details>

### 第 19 步：钱包的负面测试：不带 attestation、parent 直接解密私钥（在parent上）

wallet.json 里的加密私钥谁都能拿到（parent 能读 Sandbox 的盘），但没有匹配的 attestation 就解不开。

```bash
bash parent/attest.sh wallet-noattest
bash parent/attest.sh parent-wallet-decrypt
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh wallet-noattest
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
$ bash parent/attest.sh parent-wallet-decrypt
aws: [ERROR]: An error occurred (AccessDeniedException) when calling the Decrypt operation: User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
```
</details>

### 第 20 步：KMS Sign 签 ETH 交易（私钥不出 KMS）（在parent上）

另一种做法：私钥一直在 KMS 里，Sandbox 只把交易哈希交给 KMS Sign，再自己拼出 EIP-1559 签名。第一行是 KMS 地址，第 26 步要往这个地址打测试币。

```bash
bash parent/attest.sh eth-sign
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh eth-sign
KMS 地址      : 0xcE938fDD4ab92d443e77999a436F1C934782574c
签名哈希      : 0x985e3c0620de3fbaf7c3254d7ab8b6b6eea58c3a65bc4fb25c2f039b1c41a9bd
交易哈希      : 0x9400ca32619d8c0fc979b4988cb8f05112ff39b5dccde9b10b635903c2dd054d
原始交易      : 0x02f87583aa36a780843b9aca008506fc23ac0082520894000000000000000000000000000000000000dead87038d7ea4c6800080c001a0fdf0382fad7760581d155bd284e5bd8d9218f69340b9194c78900af87beeb4fda04cb999701322731ac269d0368c08c5e1fb4c27f524f6f8d46589709fdd6a25e3
eth-account 验证发送方: 0xcE938fDD4ab92d443e77999a436F1C934782574c OK
```
</details>

### 第 21 步：对照：parent 用同一个角色也能让 KMS 签（在parent上）

kms:Sign 不支持 attestation（Recipient），key policy 只能按 IAM 身份放行，而 parent 和 Sandbox 用的是同一个实例角色。这一步预期是成功的：KMS Sign 方案的边界是 IAM 角色，不是 Sandbox。

```bash
bash parent/attest.sh parent-eth-sign
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh parent-eth-sign
arn:aws:kms:us-west-1:111122223333:key/abcd1234-ab12-cd34-ef56-abcdef123456	ECDSA_SHA_256
```
</details>

### 第 22 步：封口：扩展并锁定 PCR16，之后 KMS 什么都不给（在parent上）

attestation 文档只包含已锁定的 PCR，所以锁定 PCR16 之后文档里多了 PCR16，命中 key policy 的 DenyAfterSeal。正式用法是服务启动时取一次密钥、马上封口。重启才会清零。

```bash
bash parent/attest.sh seal
bash parent/attest.sh decrypt
bash parent/attest.sh wallet-sign
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh seal
PCR16 封口前: 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
PCR16 封口后: 842cd3069f075282776410acd0cc120134b157c2e3bd912b3b899053850d6f7dd69f9760d5b78f11ca65dbbc741066ab （已锁定，不能再改）
从现在起到重启前，这个 Sandbox 的 attestation 都不再满足 key policy
$ bash parent/attest.sh decrypt
本次 attestation 的 PCR0 : 22e450ea930cfbcc6933363f99bf5dd788605c6c2e61d1f4731aaed3359929b64144b22866c5367a5a299d016e10fbad
本次 attestation 的 PCR16: 842cd3069f075282776410acd0cc120134b157c2e3bd912b3b899053850d6f7dd69f9760d5b78f11ca65dbbc741066ab（已封口）
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
$ bash parent/attest.sh wallet-sign
本次 attestation 的 PCR0: 22e450ea930cfbcc… （文档里有 PCR16 = 已封口）
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
```
</details>

### 第 23 步：改镜像：cmdline 多一个 quiet，PCR0 就变了（在parent上）

内核、initrd、cmdline 任何一点改动都会改变 PCR0。用改过的配置重启，同样的密文和钱包都解不开了。

```bash
bash parent/attest.sh restart changed
bash parent/attest.sh probe | grep PCR0
bash parent/attest.sh decrypt
bash parent/attest.sh wallet-sign
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh restart changed
Sandbox 已启动（changed）
$ bash parent/attest.sh probe | grep PCR0
  PCR0  fe27a4a9a1de1218d26a093363c3a71c25f072e0dd163bfafaf9799771286fee1494202f90b74c6552dba29a8bb37f0b
$ bash parent/attest.sh decrypt
本次 attestation 的 PCR0 : fe27a4a9a1de1218d26a093363c3a71c25f072e0dd163bfafaf9799771286fee1494202f90b74c6552dba29a8bb37f0b
本次 attestation 的 PCR16: 不在文档里（未封口）
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
$ bash parent/attest.sh wallet-sign
本次 attestation 的 PCR0: fe27a4a9a1de1218… 
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
```
</details>

### 第 24 步：debug 模式：PCR0/1/2 全是零（在parent上）

debug 模式下 parent 能看控制台，所以 Nitro 把 PCR0～2 清零，KMS 自然不认。

```bash
bash parent/attest.sh restart debug
bash parent/attest.sh probe
bash parent/attest.sh decrypt
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh restart debug
Sandbox 已启动（debug）
$ bash parent/attest.sh probe
attestation 文档: 4756 字节, module_id=i-0123456789abcdef0-enc0123456789abcdef, digest=SHA384
  PCR3  9f81fb901a162c0df7b1652eb155e6dba797a46a0cfaf0e9c20ae669734f4afa17a23152a203fc1d6911e3b12c6674c4
  PCR4  b6a321937c4fc1d04ef0823e880c7db5c91d36c36f4de05107ff667474c36453c5314d8030bd2e8d133990b26421c351
  全零的 PCR: [0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]（debug_mode 下 0/1/2 全零是正常的）
KMS GenerateRandom(Recipient) OK：在 Sandbox 里解开了 32 字节 → KMS 接受这份 attestation 文档
$ bash parent/attest.sh decrypt
本次 attestation 的 PCR0 : 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
本次 attestation 的 PCR16: 不在文档里（未封口）
KMS 拒绝: AccessDeniedException - User: arn:aws:sts::111122223333:assumed-role/nitro-sandbox-workshop/i-0123456789abcdef0 is not authorized to perform: kms:Decrypt on resource: arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab with an explicit deny in a resource-based policy
```
</details>

### 第 25 步：恢复原配置重启：PCR0 和第一次一样，又能解开了（在parent上）

同样的内核、initrd、cmdline，PCR0 每次启动都一样；封口也在重启后清零。

```bash
bash parent/attest.sh restart normal
bash parent/attest.sh decrypt
bash parent/attest.sh wallet-sign
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh restart normal
Sandbox 已启动（normal）
$ bash parent/attest.sh decrypt
本次 attestation 的 PCR0 : 22e450ea930cfbcc6933363f99bf5dd788605c6c2e61d1f4731aaed3359929b64144b22866c5367a5a299d016e10fbad
本次 attestation 的 PCR16: 不在文档里（未封口）
KeyId   : arn:aws:kms:us-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab
解密结果: hello nitro sandbox
$ bash parent/attest.sh wallet-sign
本次 attestation 的 PCR0: 22e450ea930cfbcc… 
钱包地址      : 0x6B43959455acf40c93561D5fb51876b31e671933
交易哈希      : 0xcaddd9c9a603fb1898103ab4a729c7f75904904d3dc9f2ef8885272817e8c315
原始交易      : 0x02f87583aa36a780843b9aca008506fc23ac008252089400000000000000000000…
eth-account 验证发送方: 0x6B43959455acf40c93561D5fb51876b31e671933 OK
```
</details>

### 第 26 步：（可选）在 Sepolia 上真的转一笔（在parent上）

先去 Sepolia 水龙头给第 20 步打印的 KMS 地址打一点测试币（0.005 ETH 就够）。--rpc 会从链上取 nonce、chainId、gas 价格，并用 eth_estimateGas 估算 gas（转给新地址要 ~20 万 gas，写死 21000 会失败）。等十几秒再查回执。

```bash
bash parent/attest.sh eth-send
bash parent/attest.sh eth-receipt <上面打印的交易哈希>
```

<details><summary>预期输出</summary>

```text
$ bash parent/attest.sh eth-send
KMS 地址      : 0xcE938fDD4ab92d443e77999a436F1C934782574c
gas 上限      : 23100 （估算 21000）
签名哈希      : 0xdfc676c125912f9a1cfb2e328f6d8cc5a22e82f6175d06781ec67adb1805c00b
交易哈希      : 0x0de54468a5cb15ff6908c03213251406d9668fc7314512feca8d876afb1e04c5
原始交易      : 0x02f86c83aa36a780021e825a3c94000000000000000000000000000000000000dead87038d7ea4c6800080c001a03e40a4017b76cd0a9060a59ec9fe5eb3d925c9745a72d6a82166ad8fb30420eda070a8a30c857dc825005cf3fa6bdaae2069d338b4bdb26853073d212ba7609564
eth-account 验证发送方: 0xcE938fDD4ab92d443e77999a436F1C934782574c OK
已广播        : 0x0de54468a5cb15ff6908c03213251406d9668fc7314512feca8d876afb1e04c5
$ bash parent/attest.sh eth-receipt 0x0de54468a5cb15ff6908c03213251406d9668fc7314512feca8d876afb1e04c5
区块 12151821  status 0x1（0x1 = 成功）  from 0xce938fdd4ab92d443e77999a436f1c934782574c
```
</details>

### 第 27 步：在 CloudTrail 里看是谁签的（在工作机上）

Sandbox 的签名和 parent 的签名，在 KMS 看来身份一样（同一个实例角色）、来源 IP 也一样（都从 parent 出去）。事件要 5～15 分钟才出现；下面同一秒的两行就是第 20、21 步。

```bash
bash workstation/cloudtrail.sh
```

脚本：[workstation/cloudtrail.sh](workstation/cloudtrail.sh)

<details><summary>预期输出</summary>

```text
2026-11-17T03:39:35Z 203.0.113.25 nitro-sandbox-workshop/i-0123456789abcdef0 成功
2026-11-17T03:39:35Z 203.0.113.25 nitro-sandbox-workshop/i-0123456789abcdef0 成功
```
</details>

## 清理

### 第 28 步：清理（在工作机上）

删掉 parent 实例（instance store 上的数据一起消失），attestation key 7 天后删除（期间可以 aws kms cancel-key-deletion 撤销）。加 --all 会连 ETH key 和实例角色一起删。

```bash
bash workstation/cleanup.sh --all
```

脚本：[workstation/cleanup.sh](workstation/cleanup.sh)

<details><summary>预期输出</summary>

```text
shutting-down
2026-11-24T11:51:13.451000+08:00
2026-11-24T11:51:15.707000+08:00
角色 nitro-sandbox-workshop 已删除
清理完成
```
</details>

## 常见问题

| 现象 | 原因 | 怎么办 |
|---|---|---|
| Firecracker 报 `No space left on device (os error 28)` | 没预留内存池 | 做第 5 步（会重启） |
| Sandbox 写入只有直测的 25% 左右（i7i.2xlarge 约 425 MB/s） | 把裸盘直接交给了 Sandbox：parent 回写裸块设备的缓存时拆成约 3 KiB 的小请求，被盘的写 IOPS 上限卡住（parent 自己这样写也一样慢） | 数据盘用 XFS 上的文件（第 9 步的 start-sandbox.sh / data-disk.sh 已经这样做） |
| 大机型上大量写入时 parent 卡死（SSH 都连不上） | 内存池很大时，按比例算的 dirty 门槛把内存池的空闲内存也算进去，但 page cache 用不了内存池，dirty 页占满剩下的内存 | 第 5 步已经把 dirty 改成固定值（`vm.dirty_bytes=2G` / `vm.dirty_background_bytes=512M`） |
| Sandbox 里 4K 随机读只有 1～4 万 IOPS | 数据盘没写 io_engine，默认是 Sync，一次只处理一个请求 | 用 parent/start-sandbox.sh 生成的配置（`"io_engine": "Async"`） |
| Sandbox 里看到的 vdb 只有 30G | NVMe 编号重启后变了，拿到的是 EBS 根盘 | 用 `/dev/disk/by-id/…Instance_Storage_…`（第 6 步） |
| 读的数字比预期高很多 | 读到了没写过的区域（SSD 不读闪存直接返回），或者 parent 的 page cache 太大 | bench.sh 第一项就是写满测试区；内存池按 config.sh 留 48G，每项前清 cache |
| Sandbox 里写完 fsync 很快，但数据其实没落盘 | cache_type 默认 Unsafe，会忽略 guest 的 flush | `"cache_type": "Writeback"`（start-sandbox.sh 已经写了） |
| ssh 连不上 172.16.0.2 | tap0 没配，或者 Sandbox 还在启动 | start-sandbox.sh 会等 2 分钟；看 `~/bench/fc.log` |
| 带 attestation 的正常解密也被拒 | key policy 里的 PCR0 和这次启动的不一样（换了内核、initrd 或 cmdline，或者 AMI 升级了内核） | 重新跑探针，用新的 PCR0 再执行一次 create-attest-key.sh（会更新 policy） |
| Sandbox 里拿不到凭证，IMDS 超时 | 实例的 IMDS hop limit 是 1；Sandbox 经过 NAT 要两跳 | launch-parent.sh 已经设了 `HttpPutResponseHopLimit=2` |
| Sepolia 上的交易 status 是 0x0 | gas 给少了：转给从没用过的地址要 ~20 万 gas | kms_sign_tx.py 带 --rpc 时会用 eth_estimateGas（已修好） |
| 封口之后还能解开 | 只扩展了 PCR16 没锁定（文档里只有已锁定的 PCR），或者 key policy 里没有 DenyAfterSeal | 用 attest.sh seal（先扩展再锁定）和 create-attest-key.sh 的完整策略 |
| AWS CLI 输出停在一个分页界面 | AWS CLI v2 默认用 less 分页 | config.sh 里已经设了 `AWS_PAGER=""` |

## 安全边界（看结果时要记住）

- attestation 证明的是**启动时的镜像**（内核、initrd、cmdline）。同样的镜像每次启动 PCR0 都一样（换一台实例也一样），所以能写进 key policy
- **rootfs 不在度量范围内**。这个 workshop 的 rootfs 里还有 sshd 和 Python，能进 Sandbox 的人照样能调 KMS。正式使用要把服务放进 initrd（或用 dm-verity，把 root hash 写进 cmdline），去掉 SSH，并在启动取完密钥后马上封口（PCR16）
- **KMS 分不出 Sandbox 和 parent**：两者用同一个实例角色、同一个出口 IP，只有 attestation 文档能区分。所以 KMS Sign 方案的边界是 IAM 角色，parent 也能签；attestation 钱包方案里 parent 拿不到私钥，但私钥会出现在 Sandbox 内存里
- Sandbox 的磁盘读写都经过 parent（Firecracker + page cache），parent 能读到明文。要保密就在 Sandbox 里用 dm-crypt；要防篡改再加 dm-integrity

## 重新生成文档（维护者）

```bash
python3 tools/build_docs.py      # 读 docs/expected/ 和 docs/data/，写 README.md 和 docs/index.html
```
