#!/usr/bin/env python3
"""生成 README.md 和 docs/index.html（维护者用：python3 tools/build_docs.py）。
步骤执行的就是仓库里的脚本；预期输出读 docs/expected/（实测日志脱敏），图表数字读 docs/data/results.json。"""
import html
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(REPO, "docs", "expected")
DATA = json.load(open(os.path.join(REPO, "docs", "data", "results.json")))
LARGE = json.load(open(os.path.join(REPO, "docs", "data", "large.json")))
GH = "https://github.com/sunchaoqun/nitro-sandbox"


def read(path):
    return open(os.path.join(REPO, path)).read().rstrip("\n")


def exp(name):
    return open(os.path.join(EXP, name + ".txt")).read().rstrip("\n")


def transcript(*pairs):
    return "\n".join(f"$ {cmd}\n{exp(name)}" for cmd, name in pairs)


# ---------------- 步骤 ----------------
TX_NOTE = "交易哈希、地址和 PCR16 每次都不一样；PCR0 只要内核、initrd、cmdline 一样就一样"
STEPS = [
    # 准备
    dict(part="prep", id="s-clone", where="工作机", title="clone 仓库，填好 workshop.env",
         why="工作机要装好 AWS CLI v2（已配置好管理员凭证）、git、python3。workshop.env 里填 parent 用的子网、安全组和 EC2 key pair；"
             "安全组要放行你电脑的 22 端口。workshop.env 在 .gitignore 里，不会被提交。",
         cmd=f"git clone {GH}.git\ncd nitro-sandbox\ncp workshop.env.example workshop.env\nvi workshop.env        # 改 AWS_REGION、SUBNET_ID、SG_ID、KEY_NAME、PEM",
         scripts=["workshop.env.example", "config.sh"], out=None),
    dict(part="prep", id="s-prereq", where="工作机", title="创建实例角色和 ETH 签名 key",
         why="ETH key 是 KMS 里的 secp256k1 密钥，私钥不出 KMS。实例角色只能对它做 Sign/GetPublicKey，外加 GenerateRandom（NSM 探针用）。"
             "已经有了会跳过，可以重复执行。",
         cmd="bash workstation/prereq.sh", scripts=["workstation/prereq.sh"],
         out=exp("w-prereq") + "\n\n# 再执行一次：\n" + exp("w-prereq-rerun")),
    dict(part="prep", id="s-launch", where="工作机", title="开一台带 instance store 的 parent",
         why="默认 i7i.2xlarge：自带一块 1875 GB 的 NVMe instance store，支持 Nitro Enclave（Sandbox 走同一个开关）。"
             "AMI 用最新的 AL2023 kernel-6.18，它的驱动才支持 Sandbox。实例 ID 会记在仓库根目录的 .parent 里，清理时要用。",
         cmd="bash workstation/launch-parent.sh", scripts=["workstation/launch-parent.sh"], out=exp("w-launch")),
    dict(part="prep", id="s-pclone", where="parent", title="登录 parent，clone 仓库",
         why="用上一步打印的 ssh 命令登录。parent 上也要一份仓库，后面的脚本都从这里执行。",
         cmd=f"sudo dnf install -y -q git\ngit clone {GH}.git\ncd nitro-sandbox", scripts=[], out=exp("p-clone")),
    dict(part="prep", id="s-setup", where="parent", title="装 fio 和 Firecracker，预留内存池（会重启）",
         why="Sandbox 的内存来自驱动预留的连续内存池，不预留就起不来。脚本还会把 dirty 门槛改成固定值（vm.dirty_bytes=2G / vm.dirty_background_bytes=512M），"
             "大内存池时默认按比例的门槛会让 parent 在大量写入时卡死（见常见问题）。最后会重启 parent，1 分钟后重新登录，并 cd ~/nitro-sandbox。",
         cmd="bash parent/setup.sh", scripts=["parent/setup.sh"], out=exp("p-setup")),
    dict(part="prep", id="s-reboot", where="parent", title="确认内存池，记下 instance store 的稳定路径",
         why="NVMe 的编号每次开机都可能变：实测重启后 nvme1n1 一次是 instance store，一次是 EBS 根盘。"
             "所以后面一律用按序列号的 /dev/disk/by-id 路径，脚本会确认型号是 Instance Storage。",
         cmd="cd ~/nitro-sandbox\nbash parent/after-reboot.sh", scripts=["parent/after-reboot.sh"], out=exp("p-after-reboot")),
    dict(part="prep", id="s-guest", where="parent", title="准备 Sandbox 的内核、initrd 和 rootfs",
         why="内核直接用 parent 自己的 /boot/vmlinuz，dracut 打一个带 virtio 和 nsm 驱动的 initrd；"
             "rootfs 是 Firecracker CI 的 Ubuntu 24.04，chroot 进去装好 fio。大约 30 秒，debconf 的提示可以忽略。",
         cmd="bash parent/build-guest.sh", scripts=["parent/build-guest.sh"], out=exp("p-build-guest")),
    # A
    dict(part="A", id="s-local", where="parent", title="测 parent 直测（这时 Sandbox 还没开）",
         why="直接读写 instance store 裸盘（O_DIRECT），作为对比基线。会清空盘上的数据（包括 XFS，之后 start-sandbox.sh 会重新格式化）。大约 4 分钟。",
         cmd="bash parent/bench.sh local", scripts=["parent/bench.sh"], out=exp("a-bench-local")),
    dict(part="A", id="s-start", where="parent", title="启动 Sandbox，数据盘是 instance store 上 XFS 里的文件",
         why="data-disk.sh 把 instance store 格式化成 XFS（第一次）、挂到 /mnt/instance-store，预分配一个大文件给 Sandbox 当数据盘。"
             "不要把裸盘直接交给 Sandbox：parent 回写裸块设备的缓存时会拆成约 3 KiB 的小请求，写入只有直测的 1/4（见 A 的结果）。"
             "Sandbox 是 confidential 模式（非 debug），4 个独占 vCPU，4 GiB 内存；数据盘 io_engine 设成 Async，cache_type 设成 Writeback（让 Sandbox 里的 fsync 真正落盘）。",
         cmd="bash parent/start-sandbox.sh", scripts=["parent/start-sandbox.sh", "parent/data-disk.sh"], out=exp("a-start-sandbox")),
    dict(part="A", id="s-sandbox", where="parent", title="测 Sandbox",
         why="同一块盘、同样的 fio 参数，这次在 Sandbox 里对 /dev/vdb 跑。大约 5 分钟。",
         cmd="bash parent/bench.sh sandbox", scripts=[], out=exp("a-bench-sandbox")),
    dict(part="A", id="s-compare", where="parent", title="对比结果",
         why="i7i.2xlarge 上：顺序读写和 4K 随机写应该和直测差不多（95～101%），4K 随机读在 80% 左右。",
         cmd="python3 parent/compare.py", scripts=["parent/compare.py"], out=exp("a-compare")),
    dict(part="A", id="s-sync", where="parent", title="（可选）看看默认的 Sync 引擎有多慢",
         why="把 io_engine 去掉再测一次随机读和顺序读。这一步说明了为什么启动 Sandbox 那一步一定要写 Async。",
         cmd="bash parent/sync-test.sh", scripts=["parent/sync-test.sh"], out=exp("a-sync")),
    # B
    dict(part="B", id="s-pydeps", where="parent", title="给 rootfs 装 Python 依赖和脚本，配好 NAT",
         why="把 sandbox/ 下的 4 个 Python 脚本装进 Sandbox 的 rootfs；Sandbox 要经 parent 的 NAT 访问 IMDS（实例角色的凭证）、KMS 和以太坊 RPC。"
             "rootfs 不在 PCR 的度量范围内，所以装依赖不影响 PCR0。大约 30 秒。",
         cmd="bash parent/attest-setup.sh", scripts=["parent/attest-setup.sh", "parent/attest.sh", "sandbox/nitro_attest.py",
                                                     "sandbox/kms_attest_poc.py", "sandbox/kms_attest_wallet.py", "sandbox/kms_sign_tx.py"],
         out=exp("b-attest-setup")),
    dict(part="B", id="s-probe", where="parent", title="启动 Sandbox，跑 NSM 探针，记下 PCR0",
         why="Sandbox 跟 /dev/nsm 要一份 attestation 文档，打印里面的 PCR，再用它调一次 KMS GenerateRandom(Recipient)。"
             "能跑通说明 KMS 接受这台 Sandbox 的 attestation。复制 PCR0 那一行的 96 个字符，下一步要用。",
         cmd="bash parent/attest.sh restart normal\nbash parent/attest.sh probe", scripts=[],
         out="$ bash parent/attest.sh restart normal\n" + exp("b-probe").split("\n", 1)[0] + "\n$ bash parent/attest.sh probe\n" + exp("b-probe").split("\n", 1)[1]),
    dict(part="B", id="s-key", where="工作机", title="创建只认这个 PCR0 的 KMS key",
         why="key policy 五条：管理员能管理但不能解密；谁都能加密；只有 PCR0 匹配的 Sandbox 能解密和生成钱包；"
             "PCR0 不匹配（包括根本没带 attestation）一律拒绝；文档里出现 PCR16（封口）一律拒绝。别名已经存在时只更新 key policy。",
         cmd="bash workstation/create-attest-key.sh <上一步的 PCR0>", scripts=["workstation/create-attest-key.sh"],
         out=exp("w-create-key") + "\n\n# 再执行一次（别名已存在，只更新 key policy）：\n" + exp("w-create-key-rerun")),
    dict(part="B", id="s-dec", where="parent", title="加密，再带 attestation 解密",
         why="加密不需要 attestation；解密时 Sandbox 把 attestation 文档（带一把临时 RSA 公钥）交给 KMS，"
             "KMS 核对 PCR0 后只把明文加密给那把公钥，所以明文只在 Sandbox 里出现。",
         cmd="bash parent/attest.sh encrypt \"hello nitro sandbox\"\nbash parent/attest.sh decrypt", scripts=[],
         out=transcript(("bash parent/attest.sh encrypt \"hello nitro sandbox\"", "b-encrypt"), ("bash parent/attest.sh decrypt", "b-decrypt"))),
    dict(part="B", id="s-neg", where="parent", title="负面测试：不带 attestation、parent 直接解密",
         why="同一个实例角色、同一段密文：Sandbox 里不带 attestation 解密，以及 parent 自己调 KMS 解密，都必须被拒（explicit deny）。",
         cmd="bash parent/attest.sh decrypt-noattest\nbash parent/attest.sh parent-decrypt", scripts=[],
         out=transcript(("bash parent/attest.sh decrypt-noattest", "b-decrypt-noattest"), ("bash parent/attest.sh parent-decrypt", "b-parent-decrypt"))),
    dict(part="B", id="s-wallet", where="parent", title="attestation 保护的 ETH 钱包：创建并签名",
         why="KMS GenerateDataKeyPair(ECC_SECG_P256K1) 只把私钥加密给 attestation 里的公钥；之后每次签名先带 attestation 解密，"
             "在 Sandbox 内存里签 EIP-1559 交易，再用 eth-account 独立验证发送方。",
         cmd="bash parent/attest.sh wallet-create\nbash parent/attest.sh wallet-sign", scripts=[],
         out=transcript(("bash parent/attest.sh wallet-create", "b-wallet-create"), ("bash parent/attest.sh wallet-sign", "b-wallet-sign"))),
    dict(part="B", id="s-walletneg", where="parent", title="钱包的负面测试：不带 attestation、parent 直接解密私钥",
         why="wallet.json 里的加密私钥谁都能拿到（parent 能读 Sandbox 的盘），但没有匹配的 attestation 就解不开。",
         cmd="bash parent/attest.sh wallet-noattest\nbash parent/attest.sh parent-wallet-decrypt", scripts=[],
         out=transcript(("bash parent/attest.sh wallet-noattest", "b-wallet-noattest"), ("bash parent/attest.sh parent-wallet-decrypt", "b-parent-wallet-decrypt"))),
    dict(part="B", id="s-sign", where="parent", title="KMS Sign 签 ETH 交易（私钥不出 KMS）",
         why="另一种做法：私钥一直在 KMS 里，Sandbox 只把交易哈希交给 KMS Sign，再自己拼出 EIP-1559 签名。第一行是 KMS 地址，第 26 步要往这个地址打测试币。",
         cmd="bash parent/attest.sh eth-sign", scripts=[], out=transcript(("bash parent/attest.sh eth-sign", "b-eth-sign"))),
    dict(part="B", id="s-signneg", where="parent", title="对照：parent 用同一个角色也能让 KMS 签",
         why="kms:Sign 不支持 attestation（Recipient），key policy 只能按 IAM 身份放行，而 parent 和 Sandbox 用的是同一个实例角色。"
             "这一步预期是成功的：KMS Sign 方案的边界是 IAM 角色，不是 Sandbox。",
         cmd="bash parent/attest.sh parent-eth-sign", scripts=[], out=transcript(("bash parent/attest.sh parent-eth-sign", "b-parent-eth-sign"))),
    dict(part="B", id="s-seal", where="parent", title="封口：扩展并锁定 PCR16，之后 KMS 什么都不给",
         why="attestation 文档只包含已锁定的 PCR，所以锁定 PCR16 之后文档里多了 PCR16，命中 key policy 的 DenyAfterSeal。"
             "正式用法是服务启动时取一次密钥、马上封口。重启才会清零。",
         cmd="bash parent/attest.sh seal\nbash parent/attest.sh decrypt\nbash parent/attest.sh wallet-sign", scripts=[],
         out=transcript(("bash parent/attest.sh seal", "b-seal"), ("bash parent/attest.sh decrypt", "b-sealed-decrypt"), ("bash parent/attest.sh wallet-sign", "b-sealed-wallet-sign"))),
    dict(part="B", id="s-changed", where="parent", title="改镜像：cmdline 多一个 quiet，PCR0 就变了",
         why="内核、initrd、cmdline 任何一点改动都会改变 PCR0。用改过的配置重启，同样的密文和钱包都解不开了。",
         cmd="bash parent/attest.sh restart changed\nbash parent/attest.sh probe | grep PCR0\nbash parent/attest.sh decrypt\nbash parent/attest.sh wallet-sign", scripts=[],
         out=transcript(("bash parent/attest.sh restart changed", "b-restart-changed"), ("bash parent/attest.sh probe | grep PCR0", "b-changed-pcr0"),
                        ("bash parent/attest.sh decrypt", "b-changed-decrypt"), ("bash parent/attest.sh wallet-sign", "b-changed-wallet-sign"))),
    dict(part="B", id="s-debug", where="parent", title="debug 模式：PCR0/1/2 全是零",
         why="debug 模式下 parent 能看控制台，所以 Nitro 把 PCR0～2 清零，KMS 自然不认。",
         cmd="bash parent/attest.sh restart debug\nbash parent/attest.sh probe\nbash parent/attest.sh decrypt", scripts=[],
         out=transcript(("bash parent/attest.sh restart debug", "b-restart-debug"), ("bash parent/attest.sh probe", "b-debug-pcr"), ("bash parent/attest.sh decrypt", "b-debug-decrypt"))),
    dict(part="B", id="s-restore", where="parent", title="恢复原配置重启：PCR0 和第一次一样，又能解开了",
         why="同样的内核、initrd、cmdline，PCR0 每次启动都一样；封口也在重启后清零。",
         cmd="bash parent/attest.sh restart normal\nbash parent/attest.sh decrypt\nbash parent/attest.sh wallet-sign", scripts=[],
         out=transcript(("bash parent/attest.sh restart normal", "b-restart-normal"), ("bash parent/attest.sh decrypt", "b-restored-decrypt"), ("bash parent/attest.sh wallet-sign", "b-restored-wallet-sign"))),
    dict(part="B", id="s-send", where="parent", title="（可选）在 Sepolia 上真的转一笔",
         why="先去 Sepolia 水龙头给第 20 步打印的 KMS 地址打一点测试币（0.005 ETH 就够）。--rpc 会从链上取 nonce、chainId、gas 价格，"
             "并用 eth_estimateGas 估算 gas（转给新地址要 ~20 万 gas，写死 21000 会失败）。等十几秒再查回执。",
         cmd="bash parent/attest.sh eth-send\nbash parent/attest.sh eth-receipt <上面打印的交易哈希>", scripts=[],
         out=transcript(("bash parent/attest.sh eth-send", "b-eth-send"), ("bash parent/attest.sh eth-receipt 0x0de54468a5cb15ff6908c03213251406d9668fc7314512feca8d876afb1e04c5", "b-eth-receipt"))),
    dict(part="B", id="s-trail", where="工作机", title="在 CloudTrail 里看是谁签的",
         why="Sandbox 的签名和 parent 的签名，在 KMS 看来身份一样（同一个实例角色）、来源 IP 也一样（都从 parent 出去）。"
             "事件要 5～15 分钟才出现；下面同一秒的两行就是第 20、21 步。",
         cmd="bash workstation/cloudtrail.sh", scripts=["workstation/cloudtrail.sh"], out=exp("w-cloudtrail")),
    # 清理
    dict(part="clean", id="s-clean", where="工作机", title="清理",
         why="删掉 parent 实例（instance store 上的数据一起消失），attestation key 7 天后删除（期间可以 aws kms cancel-key-deletion 撤销）。"
             "加 --all 会连 ETH key 和实例角色一起删。",
         cmd="bash workstation/cleanup.sh --all", scripts=["workstation/cleanup.sh"], out=exp("w-cleanup")),
]
NUM = {st["id"]: i for i, st in enumerate(STEPS, 1)}
PARTS = [("prep", "准备"), ("A", "A. instance store 性能"), ("B", "B. Attestation 和 ETH 签名"), ("clean", "清理")]

PITFALLS = [
    ("Firecracker 报 <code>No space left on device (os error 28)</code>", "没预留内存池", "做第 {s-setup} 步（会重启）"),
    ("Sandbox 写入只有直测的 25% 左右（i7i.2xlarge 约 425 MB/s）",
     "把裸盘直接交给了 Sandbox：parent 回写裸块设备的缓存时拆成约 3 KiB 的小请求，被盘的写 IOPS 上限卡住（parent 自己这样写也一样慢）",
     "数据盘用 XFS 上的文件（第 {s-start} 步的 start-sandbox.sh / data-disk.sh 已经这样做）"),
    ("大机型上大量写入时 parent 卡死（SSH 都连不上）",
     "内存池很大时，按比例算的 dirty 门槛把内存池的空闲内存也算进去，但 page cache 用不了内存池，dirty 页占满剩下的内存",
     "第 {s-setup} 步已经把 dirty 改成固定值（<code>vm.dirty_bytes=2G</code> / <code>vm.dirty_background_bytes=512M</code>）"),
    ("Sandbox 里 4K 随机读只有 1～4 万 IOPS", "数据盘没写 io_engine，默认是 Sync，一次只处理一个请求", "用 parent/start-sandbox.sh 生成的配置（<code>\"io_engine\": \"Async\"</code>）"),
    ("Sandbox 里看到的 vdb 只有 30G", "NVMe 编号重启后变了，拿到的是 EBS 根盘", "用 <code>/dev/disk/by-id/…Instance_Storage_…</code>（第 {s-reboot} 步）"),
    ("读的数字比预期高很多", "读到了没写过的区域（SSD 不读闪存直接返回），或者 parent 的 page cache 太大", "bench.sh 第一项就是写满测试区；内存池按 config.sh 留 48G，每项前清 cache"),
    ("Sandbox 里写完 fsync 很快，但数据其实没落盘", "cache_type 默认 Unsafe，会忽略 guest 的 flush", "<code>\"cache_type\": \"Writeback\"</code>（start-sandbox.sh 已经写了）"),
    ("ssh 连不上 172.16.0.2", "tap0 没配，或者 Sandbox 还在启动", "start-sandbox.sh 会等 2 分钟；看 <code>~/bench/fc.log</code>"),
    ("带 attestation 的正常解密也被拒", "key policy 里的 PCR0 和这次启动的不一样（换了内核、initrd 或 cmdline，或者 AMI 升级了内核）", "重新跑探针，用新的 PCR0 再执行一次 create-attest-key.sh（会更新 policy）"),
    ("Sandbox 里拿不到凭证，IMDS 超时", "实例的 IMDS hop limit 是 1；Sandbox 经过 NAT 要两跳", "launch-parent.sh 已经设了 <code>HttpPutResponseHopLimit=2</code>"),
    ("Sepolia 上的交易 status 是 0x0", "gas 给少了：转给从没用过的地址要 ~20 万 gas", "kms_sign_tx.py 带 --rpc 时会用 eth_estimateGas（已修好）"),
    ("封口之后还能解开", "只扩展了 PCR16 没锁定（文档里只有已锁定的 PCR），或者 key policy 里没有 DenyAfterSeal", "用 attest.sh seal（先扩展再锁定）和 create-attest-key.sh 的完整策略"),
    ("AWS CLI 输出停在一个分页界面", "AWS CLI v2 默认用 less 分页", "config.sh 里已经设了 <code>AWS_PAGER=\"\"</code>"),
]


def stepref(t):
    return re.sub(r"\{(s-[a-z]+)\}", lambda m: str(NUM[m.group(1)]), t)


# ---------------- 结果 ----------------
R, P, SYNC = DATA["run"], DATA["prev"], DATA["sync"]
TITLES = {"6-bigfile-64g": ("大文件写入", "ext4 上写一个 64 GiB 文件，1 MiB 块，QD32，含最后的 fsync", "MB/s"),
          "1-seqwrite-64g": ("大块顺序写", "裸设备写 64 GiB，1 MiB 块，QD32，含 fsync", "MB/s"),
          "5-seqread-1m": ("顺序读", "1 MiB 块，QD32，30 秒", "MB/s"),
          "2-randread-4k": ("4K 随机读", "QD32 × 4 线程，30 秒", "IOPS"),
          "4-randwrite-4k": ("4K 随机写", "QD32 × 4 线程，30 秒，含最后的 fsync", "IOPS"),
          "3-randread-4k-qd1": ("4K 随机读延迟", "QD1，单线程，30 秒；条长是平均值", "µs")}
tests = []
for key, (title, desc, unit) in TITLES.items():
    t = {"id": key, "title": title, "desc": desc, "unit": unit, "local": R[key]["local"], "sandbox": R[key]["sandbox"]}
    if key in P:
        t["prevLocal"], t["prevSandbox"] = P[key]["local"], P[key]["sandbox"]
    if "lat_add" in R[key]:
        t["latAdd"] = R[key]["lat_add"]
    tests.append(t)
D = {t["id"]: t for t in tests}


def pct(k, src=R):
    return round(100 * src[k]["sandbox"] / src[k]["local"])


def rng(k):
    vals = sorted({pct(k)} | ({pct(k, P)} if k in P else set()))
    return f"{vals[0]}%" if len(vals) == 1 else f"{vals[0]}～{vals[-1]}%"


RANGE_NOTE = (f"另一轮是在另一台 i7i.2xlarge 上用同样的步骤测的。早先在第三台 i7i.2xlarge 上（dirty 还没改成固定值）测的写入也是 24～25%。两台机器相比，Sandbox/直测："
              f"4K 随机读 {rng('2-randread-4k')}，4K 随机写 {rng('4-randwrite-4k')}，顺序读 {rng('5-seqread-1m')}，"
              f"大块顺序写 {rng('1-seqwrite-64g')}，大文件写入 {rng('6-bigfile-64g')}。")
LAT_ADD = R["3-randread-4k-qd1"].get("lat_add", D["3-randread-4k-qd1"]["sandbox"][0] - D["3-randread-4k-qd1"]["local"][0])
X8, REQ, OLD2 = LARGE["x8"], LARGE["req"], LARGE["x2_raw_old"]
n = lambda v: v if isinstance(v, str) else f"{v:,}"
pc = lambda a, b: round(100 * a / b)
X2_SEQW_PCT, X8_SEQW_PCT = pct("1-seqwrite-64g"), pc(X8["sb_1disk"]["seqwrite"], X8["host_1disk"]["seqwrite"])
REQ_ROWS = [(m, r) for m in ("x2", "x8") for r in REQ[m]]
REQ_LABEL = {"x2": "i7i.2xlarge", "x8": "i7i.8xlarge"}
X8_ROWS = [("parent 直测：单盘", X8["host_1disk"]), ("Sandbox：单盘（XFS 文件）", X8["sb_1disk"]),
           ("parent 直测：两盘 RAID 0", X8["host_raid0"]), ("Sandbox：两块盘各一个 XFS 文件，Sandbox 里 RAID 0", X8["sb_2file_raid0"])]
X8_NOTE = (f"单位：MB/s、IOPS。Sandbox 4 vCPU，fio 参数和 A 部分一样（direct=1，64 GiB 测试区，写入含 fsync）；单盘两行来自按本手册步骤的一次完整运行，"
           f"RAID 0 两行不在 workshop 步骤里。单盘大文件写入：直测 {X8['host_bigfile']:,}、Sandbox {X8['sb_1disk_bigfile']:,} MB/s。"
           f"单盘直测顺序读波动较大（另一次测试是 {X8['host_1disk_seqread_other']:,} MB/s），所以这一行 Sandbox 比直测高不代表读得更快。"
           f"早先把裸盘直接交给 Sandbox 时：单盘顺序写 dirty 默认 {' / '.join(n(v) for v in X8['raw_old']['sb_1disk_default'])}、dirty 固定 "
           f"{min(X8['raw_old']['sb_1disk_fixed']):,}～{max(X8['raw_old']['sb_1disk_fixed']):,} MB/s；在 parent 上做 RAID 0 再当一块盘给 Sandbox，顺序读只有 {X8['raw_old']['sb_hostraid_seqread']:,} MB/s，不要这么做。")
OLD2_NOTE = (f"用裸盘时还排除过这些因素（i7i.2xlarge，Sandbox 顺序写）：dirty 默认 {' / '.join(n(v) for v in OLD2['dirty_default'])}、dirty 固定 "
             f"{' / '.join(n(v) for v in OLD2['dirty_fixed'])}、关掉写回限流 {OLD2['wbt_off']:,}、Sandbox 只给 2 vCPU {OLD2['vcpu2']:,} MB/s，parent 的 CPU 一直很闲。"
             f"盘上请求数是测试期间 /sys/block/&lt;盘&gt;/stat 的增量。")
FIX_TEXT = (f"早先版本把 instance store 的裸盘直接交给 Sandbox，测出写入只有直测的 25%（i7i.2xlarge 约 {OLD2['sb_seqwrite']:,} MB/s），并把它说成 Sandbox 的限制，这是错的。"
            f"根因是 parent 回写裸块设备的 page cache 时会拆成平均约 3 KiB 的小请求，被盘的写 IOPS 上限卡住：parent 自己这样写也只有 {OLD2['host_bufwrite']:,} MB/s。"
            f"现在 start-sandbox.sh 用 instance store 上 XFS 里的一个文件当数据盘，回写是 128 KiB 的请求，Sandbox 顺序写达到直测的 {X2_SEQW_PCT}%（i7i.2xlarge）和 {X8_SEQW_PCT}%（i7i.8xlarge）。")
RANGE_NOTE = (f"上面是按本手册步骤的一次完整运行。另外单独复测过：数据盘用 XFS 文件时 Sandbox 顺序写 i7i.2xlarge {REQ['x2'][3]['mbs']:,} MB/s、i7i.8xlarge {REQ['x8'][3]['mbs']:,} MB/s，"
              f"和这里的结果一致。")


def pcr0_of(name):
    m = re.search(r"PCR0\s*:?\s*([0-9a-f]{96})", exp(name))
    return m.group(1) if m else None


PCR_ROWS = [("第一次启动（原配置）", pcr0_of("b-probe"), "s-probe"), ("cmdline 多一个 quiet", pcr0_of("b-changed-pcr0"), "s-changed"),
            ("debug 模式", pcr0_of("b-debug-decrypt"), "s-debug"), ("恢复原配置重启", pcr0_of("b-restored-decrypt"), "s-restore")]
send, rcpt = exp("b-eth-send"), exp("b-eth-receipt")
KMS_ADDR = re.search(r"KMS 地址\s+:\s*(0x[0-9a-fA-F]{40})", send).group(1)
TX = re.search(r"已广播\s+:\s*(0x[0-9a-f]{64})", send).group(1)
BLOCK = re.search(r"区块 (\d+)", rcpt).group(1)
WALLET = re.search(r"钱包地址\s+:\s*(0x[0-9a-fA-F]{40})", exp("b-wallet-create")).group(1)
TRAIL = [l.split() for l in exp("w-cloudtrail").split("\n") if l.strip()]

MATRIX = [
    ("Sandbox，原镜像，带 attestation", [("ok", "能解开", "s-dec"), ("ok", "能创建、能签名", "s-wallet"), ("ok", "能签", "s-sign")]),
    ("Sandbox，不带 attestation", [("no", "拒绝", "s-neg"), ("no", "拒绝", "s-walletneg"), ("na", "不适用", None)]),
    ("parent，用同一个实例角色", [("no", "拒绝", "s-neg"), ("no", "拒绝", "s-walletneg"), ("warn", "也能签", "s-signneg")]),
    ("封口之后（PCR16 已锁定）", [("no", "拒绝", "s-seal"), ("no", "拒绝", "s-seal"), ("na", "不适用", None)]),
    ("改过镜像（cmdline 多一个 quiet）", [("no", "拒绝", "s-changed"), ("no", "拒绝", "s-changed"), ("na", "不适用", None)]),
    ("debug 模式（PCR0～2 全零）", [("no", "拒绝", "s-debug"), ("skip", "没测", None), ("na", "不适用", None)]),
    ("恢复原镜像重启", [("ok", "能解开", "s-restore"), ("ok", "能签名", "s-restore"), ("na", "不适用", None)]),
]
MATRIX_COLS = ["加密的数据（kms:Decrypt + attestation）", "ETH 钱包私钥（GenerateDataKeyPair / Decrypt + attestation）", "KMS Sign 签 ETH（kms:Sign，私钥不出 KMS）"]


# ---------------- HTML ----------------
def code_html(text):
    out = []
    for line in html.escape(text).split("\n"):
        m = re.match(r"^(\s*)(#.*)$", line)
        line = f'{m.group(1)}<span class="c">{m.group(2)}</span>' if m else re.sub(r"(\s{2,}#\s.*)$", r'<span class="c">\1</span>', line)
        out.append(line)
    return f'<div class="code"><button class="copy" type="button">复制</button><pre><code>{chr(10).join(out)}</code></pre></div>'


def scripts_html(paths):
    if not paths:
        return ""
    parts = []
    for p in paths:
        body = html.escape(read(p))
        parts.append(f'<details class="src"><summary>脚本内容：<a href="{GH}/blob/main/{p}">{p}</a>（{body.count(chr(10)) + 1} 行）</summary>'
                     f'<pre><code>{body}</code></pre></details>')
    return '<div class="srcs">' + "".join(parts) + "</div>"


def step_html(st):
    tag = {"工作机": "w", "parent": "p"}[st["where"]]
    outb = f'<div class="out"><div class="out-h">预期输出</div><pre>{html.escape(st["out"])}</pre></div>' if st["out"] else ""
    return f"""<li class="step" id="{st["id"]}">
  <div class="step-n" aria-hidden="true">{NUM[st["id"]]}</div>
  <div class="step-b">
    <h3>{html.escape(st["title"])} <span class="where where-{tag}">在 {st["where"]} 上</span></h3>
    <p>{stepref(st["why"])}</p>
    {code_html(st["cmd"])}
    {scripts_html(st["scripts"])}
    {outb}
  </div>
</li>"""


def cell_html(kind, text, sid):
    icon = {"ok": "✓", "no": "✕", "warn": "⚠", "na": "", "skip": ""}[kind]
    label = f'<span class="mk mk-{kind}">{icon}</span>{text}' if icon else f'<span class="na">{text}</span>'
    return (f'<td class="m-{kind}"><a href="#{sid}">{label}<span class="sn">第 {NUM[sid]} 步</span></a></td>' if sid
            else f'<td class="m-{kind}">{label}</td>')


def build_html():
    page = open(os.path.join(REPO, "tools", "template.html")).read()
    heads = "".join(f"<th>{c.split('（')[0]}<br><small>{c.split('（')[1].rstrip('）')}</small></th>" for c in MATRIX_COLS)
    matrix = (f'<table class="matrix"><thead><tr><th>谁来要</th>{heads}</tr></thead><tbody>' +
              "".join(f'<tr><th scope="row">{who}</th>{"".join(cell_html(*c) for c in cells)}</tr>' for who, cells in MATRIX) + "</tbody></table>")
    toc = "\n".join(f'<li class="toc-part"><span>{label}</span><ol>' +
                    "".join(f'<li><a href="#{st["id"]}">{NUM[st["id"]]}. {html.escape(st["title"])}</a></li>' for st in STEPS if st["part"] == k) +
                    "</ol></li>" for k, label in PARTS)
    pcrs = "".join(f'<tr><td><a href="#{sid}">{label}</a></td><td><code>{v[:16]}…{v[-8:]}</code></td>'
                   f'<td>{"和 key policy 里的一致" if v == PCR_ROWS[0][1] else "不一致 → 被拒"}</td></tr>' for label, v, sid in PCR_ROWS)
    trail = "".join(f"<tr><td>{t[0].replace('T', ' ').rstrip('Z')} UTC</td><td>{t[1]}</td><td>{t[2]}</td><td>{t[3]}</td></tr>" for t in TRAIL)
    eth = f"""<dl class="env">
  <dt>KMS Sign 地址</dt><dd><code>{KMS_ADDR}</code>（{html.escape(DATA.get("eth_alias", "alias/nitro-sandbox-workshop-eth"))}）</dd>
  <dt>真转的一笔</dt><dd><a href="https://sepolia.etherscan.io/tx/{TX}">{TX[:18]}…</a>，Sepolia 区块 {BLOCK}，status 0x1</dd>
  <dt>attestation 钱包</dt><dd><code>{WALLET}</code>（私钥只在 Sandbox 内存里出现过）</dd>
</dl>
<div class="tablewrap"><table><thead><tr><th>时间</th><th>来源 IP</th><th>身份</th><th>结果</th></tr></thead><tbody>{trail}</tbody></table></div>
<p class="note">CloudTrail 里同一秒的两次 kms:Sign：一次来自 Sandbox（第 {NUM["s-sign"]} 步），一次来自 parent（第 {NUM["s-signneg"]} 步）。身份和来源 IP 完全一样，KMS 分不出来。地址、交易哈希每个人都不一样。</p>"""
    tiles = [("4K 随机读", f"{pct('2-randread-4k')}%", f"{D['2-randread-4k']['sandbox']:,} 对 {D['2-randread-4k']['local']:,} IOPS"),
             ("顺序读", f"{pct('5-seqread-1m')}%", f"{D['5-seqread-1m']['sandbox']:,} 对 {D['5-seqread-1m']['local']:,} MB/s"),
             ("大文件写入", f"{pct('6-bigfile-64g')}%", f"{D['6-bigfile-64g']['sandbox']:,} 对 {D['6-bigfile-64g']['local']:,} MB/s"),
             ("读延迟（QD1）", f"+{LAT_ADD} µs", f"平均 {D['3-randread-4k-qd1']['sandbox'][0]} 对 {D['3-randread-4k-qd1']['local'][0]} µs")]
    tile_html = "\n".join(f'<div class="tile"><div class="tile-l">{a}</div><div class="tile-v">{b}</div><div class="tile-s">{c}</div></div>' for a, b, c in tiles)
    pits = "\n".join(f"<tr><td>{a}</td><td>{stepref(b)}</td><td>{stepref(c)}</td></tr>" for a, b, c in PITFALLS)
    rep = {
        "/*DATA*/": json.dumps({"tests": tests, "sync": SYNC}, ensure_ascii=False),
        "<!--MATRIX-->": matrix, "<!--TOC-->": toc, "<!--PCRS-->": pcrs, "<!--ETH-->": eth, "<!--TILES-->": tile_html, "<!--PITFALLS-->": pits,
        "{{NSTEPS}}": str(len(STEPS)), "{{S_SYNC}}": str(NUM["s-sync"]), "{{RANGE_NOTE}}": RANGE_NOTE,
        "{{BIGFILE_PCT}}": str(pct("6-bigfile-64g")), "{{RANDREAD_PCT}}": str(pct("2-randread-4k")), "{{SEQREAD_PCT}}": str(pct("5-seqread-1m")),
        "{{RANDWRITE_PCT}}": str(pct("4-randwrite-4k")), "{{SANDBOX_WRITE}}": str(D["6-bigfile-64g"]["sandbox"]), "{{LAT_ADD}}": str(LAT_ADD),
        "{{SYNC_X}}": str(round(D["2-randread-4k"]["sandbox"] / SYNC["randread"])),
        "{{FIX_TEXT}}": FIX_TEXT, "{{X2_SEQW_PCT}}": str(X2_SEQW_PCT), "{{X8_SEQW_PCT}}": str(X8_SEQW_PCT),
        "{{X8_SB1}}": f"{X8['sb_1disk']['seqwrite']:,}", "{{X8_SB2}}": f"{X8['sb_2file_raid0']['seqwrite']:,}", "{{X8_HOST2}}": f"{X8['host_raid0']['seqwrite']:,}",
        "{{X8_RR}}": f"{X8['sb_1disk']['randread']:,}", "{{X8_RR_HOST}}": f"{X8['host_1disk']['randread']:,}",
        "{{P99_LOCAL}}": str(D["3-randread-4k-qd1"]["local"][1]), "{{P99_SB}}": str(D["3-randread-4k-qd1"]["sandbox"][1]),
        "<!--REQ_TABLE-->": '<div class="tablewrap"><table><thead><tr><th>机型</th><th>顺序写 64 GiB（1 MiB 块，含 fsync）</th><th class="n">写入 MB/s</th><th class="n">盘上写请求/秒</th><th class="n">平均每个请求</th></tr></thead><tbody>'
                            + "".join(f'<tr><td>{REQ_LABEL[m]}</td><td>{r["label"]}</td><td class="n">{r["mbs"]:,}</td><td class="n">{r["reqs"]:,}</td><td class="n">{r["kib"]} KiB</td></tr>' for m, r in REQ_ROWS)
                            + f'</tbody></table></div><p class="note">{OLD2_NOTE}</p>',
        "<!--X8_TABLE-->": '<div class="tablewrap"><table><thead><tr><th>i7i.8xlarge</th><th class="n">顺序写</th><th class="n">顺序读</th><th class="n">4K 随机读</th><th class="n">4K 随机写</th></tr></thead><tbody>'
                           + "".join(f'<tr><td>{a}</td>' + "".join(f'<td class="n">{n(r[k])}</td>' for k in ("seqwrite", "seqread", "randread", "randwrite")) + "</tr>" for a, r in X8_ROWS)
                           + f'</tbody></table></div><p class="note">{X8_NOTE}</p>',
    }
    for k, _ in PARTS:
        rep[f"<!--STEPS:{k}-->"] = "\n".join(step_html(st) for st in STEPS if st["part"] == k)
    for a, b in rep.items():   # 模板里没用到的替换项跳过；没替换掉的占位符在下面检查
        page = page.replace(a, b)
    left = re.findall(r"\{\{[A-Z_]+\}\}|<!--[A-Z:a-z]+-->", page)
    assert not left, left
    return page


# ---------------- README.md ----------------
def build_md():
    L = []
    w = L.append
    w("# EC2 Nitro Sandbox Workshop：attestation、ETH 签名、instance store 性能\n")
    w("在一台 EC2 上从零搭起 Nitro Sandbox（Firecracker），实测三件事：\n")
    w("1. **KMS 能不能只对 PCR 匹配的 Sandbox 放行**：带 attestation 能解密，不带 attestation、parent、封口之后、改过镜像、debug 模式都被拒")
    w("2. **以太坊交易怎么签、谁能签**：KMS Sign（私钥不出 KMS）和 attestation 保护的钱包（私钥只在 Sandbox 内存里），外加 Sepolia 上真转一笔")
    w("3. **把整块 instance store 交给 Sandbox 后读写有多快**：和 parent 上直接测对比\n")
    w(f"每一步执行的都是仓库里的脚本，每一步都附了实测的预期输出（2026-11-17，在全新的账号资源上从第 1 步跑到第 {len(STEPS)} 步）。"
      f"图文版（同样的步骤，带结果矩阵和性能图表）：https://sunchaoqun.github.io/nitro-sandbox/\n")
    w(f"> **更正（2026-11-17）**：{FIX_TEXT}\n")
    w("## 结果一览\n")
    w("| 谁来要 | " + " | ".join(c.split("（")[0] for c in MATRIX_COLS) + " |")
    w("|---|---|---|---|")
    icon = {"ok": "✅", "no": "❌", "warn": "⚠️", "na": "–", "skip": "没测"}
    for who, cells in MATRIX:
        w(f"| {who} | " + " | ".join(f"{icon[k]} {t if k not in ('na', 'skip') else ''}".strip() + (f"（第 {NUM[s]} 步）" if s else "") for k, t, s in cells) + " |")
    w("\nKMS Sign 那一列只测了前三行：kms:Sign 不支持 attestation，key policy 只能按 IAM 身份放行，所以 parent 也能签。\n")
    w("| instance store（i7i.2xlarge） | parent 直测 | Sandbox | Sandbox / 直测 |")
    w("|---|---|---|---|")
    for t in tests:
        if t["unit"] == "µs":
            w(f"| {t['title']}（平均 / p99） | {t['local'][0]} / {t['local'][1]} µs | {t['sandbox'][0]} / {t['sandbox'][1]} µs | +{LAT_ADD} µs |")
        else:
            w(f"| {t['title']} | {t['local']:,} {t['unit']} | {t['sandbox']:,} {t['unit']} | {pct(t['id'])}% |")
    w(f"| 4K 随机读，默认 Sync 引擎 | – | {SYNC['randread']:,} IOPS | Async 的 1/{round(D['2-randread-4k']['sandbox'] / SYNC['randread'])} |")
    w(f"\n{RANGE_NOTE}\n")
    w("### 数据盘为什么要用 XFS 上的文件，不能用裸盘\n")
    w("Firecracker 打开数据盘时不用 O_DIRECT，Sandbox 的写入先进 parent 的 page cache，再由 parent 内核回写。回写裸块设备时请求被拆得很小，被盘的写 IOPS 上限卡住；"
      "回写 XFS 上的文件是 128 KiB 的请求，和 O_DIRECT 一样快。\n")
    w("| 机型 | 顺序写 64 GiB（1 MiB 块，含 fsync） | 写入 MB/s | 盘上写请求/秒 | 平均每个请求 |")
    w("|---|---|---|---|---|")
    for m, r in REQ_ROWS:
        w(f"| {REQ_LABEL[m]} | {r['label']} | {r['mbs']:,} | {r['reqs']:,} | {r['kib']} KiB |")
    w(f"\n{re.sub(r'&lt;|&gt;', lambda m: '<' if m.group() == '&lt;' else '>', OLD2_NOTE)}\n")
    w("### i7i.8xlarge、多盘 RAID 0\n")
    w(f"数据盘同样用 XFS 文件：单盘 Sandbox 顺序写是直测的 {X8_SEQW_PCT}%；两块盘各放一个文件、在 Sandbox 里组 RAID 0，顺序写 {X8['sb_2file_raid0']['seqwrite']:,} MB/s，"
      f"和 parent 直测两盘 RAID 0（{X8['host_raid0']['seqwrite']:,}）几乎一样。随机读是例外：每个 Sandbox 的 4K 随机读只有 25～31 万 IOPS，大机型上明显低于直测。\n")
    w("| i7i.8xlarge | 顺序写 MB/s | 顺序读 MB/s | 4K 随机读 IOPS | 4K 随机写 IOPS |")
    w("|---|---|---|---|---|")
    for a, r in X8_ROWS:
        w(f"| {a} | " + " | ".join(n(r[k]) for k in ("seqwrite", "seqread", "randread", "randwrite")) + " |")
    w(f"\n{X8_NOTE}\n")
    w("## 开始之前\n")
    w("- **工作机**（你自己的电脑或者一台跳板机）：AWS CLI v2 并配好有管理员权限的凭证、git、python3、ssh")
    w("- **区域**：默认 us-west-1；换区域就改 workshop.env 里的 AWS_REGION，并确认该区域、该子网的可用区有 i7i.2xlarge")
    w("- **网络**：一个能从工作机 ssh 进去的子网（有公网 IP，或者工作机就在同一个 VPC），安全组放行 22 端口；parent 要能访问公网（下载 Firecracker、Ubuntu 软件包、以太坊 RPC）")
    w("- **费用**：i7i.2xlarge 按需 $0.83/小时（us-west-1），全程约 1.5 小时；两把 KMS key 各 $1/月（清理后 7 天删除）；Sepolia 测试币免费")
    w("- **会创建的资源**：实例角色 `nitro-sandbox-workshop`、KMS key `alias/nitro-sandbox-workshop-eth` 和 `alias/nitro-sandbox-workshop-attest`、一台 parent 实例。名字都在 config.sh 里，可以在 workshop.env 里改")
    w("- **注意**：bench.sh 会清空 instance store；ETH 部分只用 Sepolia 测试网\n")
    w("## 仓库结构\n")
    w("```\nconfig.sh              所有脚本共用的配置（区域、机型、角色名、key 别名）\nworkshop.env.example   复制成 workshop.env，填子网、安全组、key pair\n"
      "workstation/           在工作机上执行：prereq / launch-parent / create-attest-key / cloudtrail / cleanup\n"
      "parent/                在 parent 上执行：setup / after-reboot / build-guest / bench / start-sandbox / attest-setup / attest …\n"
      "sandbox/               装进 Sandbox 的 Python：NSM attestation、KMS 加解密、attestation 钱包、KMS Sign 签 ETH\n"
      "docs/                  图文版手册（index.html）、每一步的预期输出（expected/）、性能数据（data/）\ntools/                 生成 README 和 docs 的脚本（维护者用）\n```\n")
    for k, label in PARTS:
        w(f"## {label}\n")
        for st in [s for s in STEPS if s["part"] == k]:
            w(f"### 第 {NUM[st['id']]} 步：{st['title']}（在{st['where']}上）\n")
            w(re.sub(r"<[^>]+>", "", stepref(st["why"])) + "\n")
            w("```bash\n" + st["cmd"] + "\n```\n")
            if st["scripts"]:
                w("脚本：" + "、".join(f"[{p}]({p})" for p in st["scripts"]) + "\n")
            if st["out"]:
                w("<details><summary>预期输出</summary>\n\n```text\n" + st["out"] + "\n```\n</details>\n")
    w("## 常见问题\n")
    w("| 现象 | 原因 | 怎么办 |")
    w("|---|---|---|")
    md = lambda t: re.sub(r"</?code>", "`", stepref(t)).replace("|", "\\|")
    for a, b, c in PITFALLS:
        w(f"| {md(a)} | {md(b)} | {md(c)} |")
    w("\n## 安全边界（看结果时要记住）\n")
    w("- attestation 证明的是**启动时的镜像**（内核、initrd、cmdline）。同样的镜像每次启动 PCR0 都一样（换一台实例也一样），所以能写进 key policy")
    w("- **rootfs 不在度量范围内**。这个 workshop 的 rootfs 里还有 sshd 和 Python，能进 Sandbox 的人照样能调 KMS。正式使用要把服务放进 initrd（或用 dm-verity，把 root hash 写进 cmdline），去掉 SSH，并在启动取完密钥后马上封口（PCR16）")
    w("- **KMS 分不出 Sandbox 和 parent**：两者用同一个实例角色、同一个出口 IP，只有 attestation 文档能区分。所以 KMS Sign 方案的边界是 IAM 角色，parent 也能签；attestation 钱包方案里 parent 拿不到私钥，但私钥会出现在 Sandbox 内存里")
    w("- Sandbox 的磁盘读写都经过 parent（Firecracker + page cache），parent 能读到明文。要保密就在 Sandbox 里用 dm-crypt；要防篡改再加 dm-integrity")
    w("\n## 重新生成文档（维护者）\n")
    w("```bash\npython3 tools/build_docs.py      # 读 docs/expected/ 和 docs/data/，写 README.md 和 docs/index.html\n```")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    open(os.path.join(REPO, "docs", "index.html"), "w").write(build_html())
    open(os.path.join(REPO, "README.md"), "w").write(build_md())
    print(f"已生成 README.md 和 docs/index.html（{len(STEPS)} 步）")
