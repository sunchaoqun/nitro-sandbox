# 所有脚本共用的配置。要改的话：cp workshop.env.example workshop.env，再改 workshop.env（它不会被提交到 git）
_REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[ -f "$_REPO/workshop.env" ] && source "$_REPO/workshop.env"
export AWS_REGION=${AWS_REGION:-us-west-1}
export AWS_PAGER=""                                                        # 不让 AWS CLI 把输出交给 less
INSTANCE_TYPE=${INSTANCE_TYPE:-i7i.2xlarge}                               # 要带 instance store、支持 Nitro Enclave
MEMPOOL=${MEMPOOL:-48G}                                                   # Sandbox 内存池，比实例内存少 ~16G
ROLE_NAME=${ROLE_NAME:-nitro-sandbox-workshop}                            # 实例角色 / instance profile
ETH_KEY_ALIAS=${ETH_KEY_ALIAS:-alias/nitro-sandbox-workshop-eth}          # KMS Sign 用的 secp256k1 key
ATTEST_KEY_ALIAS=${ATTEST_KEY_ALIAS:-alias/nitro-sandbox-workshop-attest} # 只认 PCR0 的对称 key
WORK=${WORK:-$HOME/bench}                                                 # parent 上的工作目录（镜像、结果）
FC_VERSION=${FC_VERSION:-v1.18.0}
