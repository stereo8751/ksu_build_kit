#!/usr/bin/env bash
# =====================================================================
# 小米6 (sagit) 带 KSU 内核 —— WSL 本地编译脚本（步骤与 GitHub Actions 等价）
#
# 前置：Windows 上先装 WSL（管理员 PowerShell 执行）：
#   wsl --install -d Ubuntu-22.04
# 装完重启进入 Ubuntu，然后在 WSL 内运行本脚本。
#
# 用法（在仓库根目录，即本脚本所在目录）：
#   bash local_build.sh                                   # 全默认（sagit-p-oss + SukiSU builtin）
#   bash local_build.sh --ksu-type tiann-0.9.5            # 改用 tiann/KernelSU v0.9.5
#   bash local_build.sh --boot-img /mnt/e/xx/boot.img     # 编译完自动重打包 boot.img
#   bash local_build.sh --branch sagit-o-oss --defconfig sdm660-perf_defconfig
# =====================================================================
set -euo pipefail

# ---------------- 默认参数（与 CI 默认一致，均已实测核对） ----------------
KERNEL_REPO="MiCode/Xiaomi_Kernel_OpenSource"
KERNEL_BRANCH="sagit-p-oss"
DEFCONFIG="sagit_user_defconfig"          # sagit 专属，CONFIG_LOCALVERSION="-perf"
DTB_NAME="sagit-msm8998-v2.1-mtp.dtb"     # 已验证现役 boot.img 内为 MSM8998 v2.1 MTP
KSU_TYPE="sukisu-builtin"                 # sukisu-builtin | tiann-0.9.5
BOOT_IMG=""
WORKDIR="$(pwd)"
TOOLCHAIN_TAG="20210522"                  # proton clang 13.0.0，与现役内核工具链一致

# ---------------- 参数解析 ----------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)       KERNEL_REPO="$2"; shift 2 ;;
        --branch)     KERNEL_BRANCH="$2"; shift 2 ;;
        --defconfig)  DEFCONFIG="$2"; shift 2 ;;
        --dtb)        DTB_NAME="$2"; shift 2 ;;
        --ksu-type)   KSU_TYPE="$2"; shift 2 ;;
        --boot-img)   BOOT_IMG="$2"; shift 2 ;;
        --workdir)    WORKDIR="$2"; shift 2 ;;
        *) echo "未知参数：$1"; exit 1 ;;
    esac
done

if [[ ! -f repack_boot.py || ! -f ksu_hooks_patch.py ]]; then
    echo "请在套件根目录运行本脚本（缺 repack_boot.py / ksu_hooks_patch.py）"; exit 1
fi
if [[ "$KSU_TYPE" != "sukisu-builtin" && "$KSU_TYPE" != "tiann-0.9.5" ]]; then
    echo "ksu-type 只能是 sukisu-builtin 或 tiann-0.9.5"; exit 1
fi

echo "=== 配置 ==="
echo "内核源码 : ${KERNEL_REPO} @ ${KERNEL_BRANCH}"
echo "defconfig: ${DEFCONFIG} | DTB: ${DTB_NAME}"
echo "KSU 方式 : ${KSU_TYPE}"
echo "工作目录 : ${WORKDIR}"
echo

# ---------------- 1. 依赖 ----------------
echo "=== [1/7] 安装编译依赖（需要 sudo） ==="
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential bc bison flex libssl-dev libncurses5-dev \
    python3 python3-dev cpio zip curl wget git

# ---------------- 2. 内核源码 ----------------
cd "${WORKDIR}"
if [[ -d kernel_src ]]; then
    echo "=== [2/7] kernel_src 已存在，跳过克隆 ==="
else
    echo "=== [2/7] 克隆内核源码 ==="
    git clone --depth=1 -b "${KERNEL_BRANCH}" \
        "https://github.com/${KERNEL_REPO}" kernel_src
fi
cd kernel_src
test -f "arch/arm64/configs/${DEFCONFIG}" || { echo "defconfig 不存在：${DEFCONFIG}"; exit 1; }

# ---------------- 3. 工具链 ----------------
cd "${WORKDIR}"
if [[ -d proton-clang ]]; then
    echo "=== [3/7] proton-clang 已存在，跳过克隆 ==="
else
    echo "=== [3/7] 克隆 proton clang（tag ${TOOLCHAIN_TAG}） ==="
    git clone --depth=1 -b "${TOOLCHAIN_TAG}" \
        "https://github.com/kdrag0n/proton-clang" proton-clang
fi
proton-clang/bin/clang --version | head -n 2

# ---------------- 4. KSU 源码集成 ----------------
cd "${WORKDIR}/kernel_src"
if [[ -d drivers/kernelsu ]]; then
    echo "=== [4/7] drivers/kernelsu 已存在，跳过 setup.sh ==="
else
    echo "=== [4/7] 集成 KSU 源码（${KSU_TYPE}） ==="
    if [[ "${KSU_TYPE}" == "tiann-0.9.5" ]]; then
        curl -LSs "https://raw.githubusercontent.com/tiann/KernelSU/main/kernel/setup.sh" \
            | bash -s v0.9.5
    else
        curl -LSs "https://raw.githubusercontent.com/SukiSU-Ultra/SukiSU-Ultra/main/kernel/setup.sh" \
            | bash -s builtin
    fi
fi
test -d drivers/kernelsu || { echo "drivers/kernelsu 未创建，setup.sh 失败"; exit 1; }

# ---------------- 5. hook 补丁 ----------------
echo "=== [5/7] 打手动集成 hook 补丁 ==="
python3 "${WORKDIR}/ksu_hooks_patch.py" --mode "${KSU_TYPE}" --src .

# ---------------- 6. CONFIG ----------------
echo "=== [6/7] 追加 KSU CONFIG ==="
DEF="arch/arm64/configs/${DEFCONFIG}"
[[ -f "${DEF}.bak" ]] || cp "$DEF" "${DEF}.bak"
grep -q '^CONFIG_KSU=y' "$DEF" || printf '\n# ==== KernelSU（手动集成，无 kprobe） ====\nCONFIG_KSU=y\n' >> "$DEF"
if [[ "${KSU_TYPE}" == "sukisu-builtin" ]]; then
    grep -q '^CONFIG_KSU_FEATURE_ADBROOT=y' "$DEF" || printf 'CONFIG_KSU_FEATURE_ADBROOT=y\n' >> "$DEF"
fi
tail -n 5 "$DEF"

# ---------------- 7. 编译 + 拼接 ----------------
echo "=== [7/7] 编译 ==="
export PATH="${WORKDIR}/proton-clang/bin:${PATH}"
# CC=clang 命令行覆盖：绕过顶层 Makefile 的 gcc-wrapper.py（python2），直接用 proton clang
MAKE="make O=out ARCH=arm64 CC=clang CLANG_TRIPLE=aarch64-linux-gnu- CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_ARM32=arm-linux-gnueabi-"
$MAKE "${DEFCONFIG}"
grep -q '^CONFIG_KSU=y' out/.config || { echo "CONFIG_KSU 未生效！检查 out/.config"; exit 1; }
JOBS="$(nproc)"
$MAKE -j"${JOBS}" Image.gz dtbs

# sagit 无 dtbo：内核 = gzip 流 + 单 DTB 拼接（与原厂 boot.img 结构一致）
cat out/arch/arm64/boot/Image.gz \
    "out/arch/arm64/boot/dts/qcom/${DTB_NAME}" \
    > out/Image.gz-dtb
ls -l out/Image.gz-dtb
head -c 2 out/Image.gz-dtb | od -A n -t x1 | grep -q "1f 8b" \
    || { echo "Image.gz-dtb 开头不是 gzip 魔数"; exit 1; }

# ---------------- 可选：重打包 boot.img ----------------
if [[ -n "${BOOT_IMG}" ]]; then
    echo "=== 重打包 boot.img（base=${BOOT_IMG}） ==="
    python3 "${WORKDIR}/repack_boot.py" \
        --kernel out/Image.gz-dtb \
        --base "${BOOT_IMG}" \
        --out boot_ksu.img
    ls -l boot_ksu.img
    echo "刷写：fastboot flash boot boot_ksu.img"
fi

echo
echo "=== 完成 ==="
echo "Image.gz-dtb : ${WORKDIR}/kernel_src/out/Image.gz-dtb"
if [[ -f boot_ksu.img ]]; then
    echo "boot_ksu.img : ${WORKDIR}/kernel_src/boot_ksu.img"
fi
