# 小米6 (sagit) 带 KernelSU/SukiSU root 的内核编译套件

为小米6（sagit / msm8998 / 内核 4.4.253-perf，非 GKI）定制的一套"带 root 的内核"云/本地编译方案。
本套件只做研究与脚本封装，不包含任何内核二进制。

## 套件内容

| 文件 | 说明 |
|---|---|
| `.github/workflows/build-kernel.yml` | GitHub Actions 云编译 workflow（路线 A，推荐） |
| `local_build.sh` | WSL 本地编译脚本（路线 B，步骤与 CI 等价） |
| `ksu_hooks_patch.py` | 手动集成 hook 补丁器（在真实 sagit-p-oss 源码上逐锚点验证通过） |
| `repack_boot.py` | 把编译出的 Image.gz-dtb 装回 boot.img（只替换 kernel 段，已自测通过） |

---

## 一、为什么非 GKI 设备必须"手动集成"

以下事实均已实测核实：

1. **官方 KernelSU 自 v1.0 起放弃非 GKI**（[官方文档](https://kernelsu.org/guide/how-to-integrate-for-non-gki.html)明确标注"archival reference only"），非 GKI 最后官方支持版本为 **v0.9.5**，无法用官方 GKI boot.img 替换法。
2. **kprobe 动态注入路线不通**：你的内核内嵌 config（CONFIG_IKCONFIG=y，可直接读 `/proc/config.gz`）显示 `CONFIG_KPROBES` 未开启、`CONFIG_BPF_SYSCALL` 未开启。在 4.4 老 MSM 内核上强行开 kprobe 风险大（依赖链复杂），官方文档也仅建议"kprobe 可靠时"才用自动注入。
3. 因此唯一稳妥路线：**把 KSU 源码静态编译进内核 + 在内核源码里手动插入 hook 调用**（manual integration），这正是本套件的做法。

> 顺带纠正一个常见过时说法：网上教程常见的 `CONFIG_KSU_MANUAL_HOOK=y` **已废弃**——SukiSU-Ultra 官方文档明确说明 `CONFIG_KSU_MANUAL_HOOK` 和 `CONFIG_KSU_TRACEPOINT_HOOK` 在现行分支中已不存在，`ksu_trace.h` 也已删除。现行做法是直接选用 `builtin` 分支（该分支本身就不注册 kprobe），手动在源码里加钩子调用。

## 二、Fork 选择（研究结论）

| Fork | 结论 | 依据（实抓） |
|---|---|---|
| **SukiSU-Ultra `builtin` 分支** ✅ **主选** | Kconfig 中 `CONFIG_KSU` 为 bool、**无任何 depends**（不要求 kprobe）；官方文档明确定位"非 GKI 手动补丁内核"；维护活跃；自带 SUSFS（挂 4.4 缺 `THREAD_INFO_IN_TASK`，自动不可用，无碍）与 KPM 可选项 | 抓取了其 `kernel/Kconfig`、`docs/guide/how-to-integrate.md`、`kernel/feature/sucompat.{c,h}`、`kernel_umount.h` 逐一核对 |
| tiann/KernelSU v0.9.5 | 回退方案（本套件 `--ksu-type tiann-0.9.5` 一键切换） | 官方非 GKI 存档文档对 4.4 以下内核给出了完整手动补丁（含 4.4 无 `do_faccessat`/`vfs_statx` 的替代挂法），hook 签名逐字核对过 |
| KernelSU-Next ❌ 排除 | README 宣称支持 4.4+，但其 `kernel/Kconfig` 写明 `config KSU: depends on KPROBES && EXT4_FS`——**驱动本身强制依赖 kprobe**，与本机"kprobe 关闭"的前提冲突 | 抓取其 dev 分支 `kernel/Kconfig` 与 `kernel/setup.sh` 核对 |

> 提示：SukiSU-Ultra 官方兼容口径是"4.14+，3.x 需更多回溯"，4.4 处于中间地带；社区已有大量 4.4 MIUI 内核用 builtin 分支编译成功的先例。若编译报错，可切 `tiann-0.9.5` 回退（其 4.4 兼容性有官方文档背书），或根据报错位置做小回溯。

## 三、已核实的源码信息（sagit）

| 项目 | 值 | 说明 |
|---|---|---|
| 源码仓库 | `MiCode/Xiaomi_Kernel_OpenSource` | |
| 分支 | **`sagit-p-oss`** | 该分支下有 sagit-n/o/p-oss 三个分支；P 版即 4.4（顶层 Makefile：VERSION=4 PATCHLEVEL=4 SUBLEVEL=153），与你的 4.4.253-perf 对应 |
| defconfig | **`arch/arm64/configs/sagit_user_defconfig`** | 注意：**没有** `sagit_defconfig`。该文件是完整 defconfig，`CONFIG_LOCALVERSION="-perf"`（对应 `-perf` 内核串）、`CONFIG_ARCH_MSM8998=y`、`CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE=y` |
| DTB | **`sagit-msm8998-v2.1-mtp.dtb`** | `arch/arm64/boot/dts/qcom` 是指向 `arch/arm/boot/dts/qcom` 的符号链接；其 Makefile 列出 `sagit-msm8998-mtp / v2 / v2.1-mtp` 三个 dtb。已解析你现役 boot.img 内嵌 DTB 的根节点 model = **"Qualcomm Technologies, Inc. MSM 8998 v2.1 MTP"**，确认量产小米6 用 v2.1 |
| 内核拼接 | `Image.gz + 单 DTB` | 未设 `CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE_NAMES` 时，`make Image.gz-dtb` 会把找到的**所有** dtb 全部拼上，因此本套件改为分别 `make Image.gz dtbs` 后手动 `cat` 指定 dtb，与原厂结构一致 |
| 工具链 | **proton clang tag `20210522`**（clang 13.0.0 + binutils 2.36.1） | kdrag0n/proton-clang 的 release **没有附件**，需 git clone（tag 与 master 终点一致）；与你现役内核的"proton clang 13.0.0"完全一致，证明该源码可用此工具链构建 |
| 构建命令要点 | `make O=out ARCH=arm64 CC=clang ...` | sagit-p-oss 顶层 Makefile 默认 `CC = scripts/gcc-wrapper.py $(REAL_CC)`（python2）；**命令行传 `CC=clang` 会覆盖它**，既绕过 python2 依赖又启用 proton clang——这正是原厂内核的构建方式 |

## 四、Hook 修改点清单

### 已验证、已自动化（ksu_hooks_patch.py，均在 sagit-p-oss 真实源文件上跑通）

| 文件 | 挂接点 | 两种模式 | 验证方式 |
|---|---|---|---|
| `fs/exec.c` | `do_execveat_common` 入口 → `ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags)` | 通用 | 拉取真实源文件跑补丁后逐行检查（注意：4.4 该函数是老式内联实现，没有 `__do_execve_file` 调用，所以插在函数体入口——与官方 kprobe 版挂接位置语义一致） |
| `fs/open.c` | `SYSCALL_DEFINE3(faccessat,...)` 入口 → `ksu_handle_faccessat(&dfd, &filename, &mode, NULL)` | 通用 | 实测确认 4.4 没有 `do_faccessat`，走 syscall 定义（与官方文档对老内核的指引一致） |
| `fs/stat.c` | `vfs_fstatat` 入口 → `ksu_handle_stat(&dfd, &filename, &flag)` | 通用 | 实测确认 4.4 没有 `vfs_statx`（4.11 才引入），用 `vfs_fstatat`；builtin 非 SUSFS 的签名恰为 `const char __user **`，吻合 |
| `fs/namespace.c` | 回溯 `path_umount`（自 5.9，官方文档做法），4.4 无 `MNT_LOCKED` 已用 `#ifdef` 兜底 | 通用 | 实测插入点在 `do_umount` 定义之后、"Now umount can handle..." 注释之前 |
| `fs/read_write.c` | `vfs_read` 入口 → `ksu_handle_vfs_read(...)` | 仅 tiann-0.9.5 | 实测插入成功，签名与官方文档一致 |
| `drivers/input/input.c` | `input_handle_event` 入口 → Safe Mode（音量下键三连击防 bootloop） | 仅 tiann-0.9.5 | 实测插入成功，签名与官方文档一致 |

SukiSU builtin 模式下 `CONFIG_KSU_SUSFS` 因依赖 `THREAD_INFO_IN_TASK`（4.4 无）而自动不可用，钩子签名统一采用非 SUSFS 形态（`const char __user **`），无二义性。

### 需人工确认的部分（诚实标注，未自动化）

1. **builtin 模式的"卸载模块"功能**：`kernel_umount.h` 已核对签名 `int ksu_handle_umount(uid_t old_uid, uid_t new_uid)`，官方文档说明它应挂在 setresuid 路径上，但 4.4 `kernel/sys.c` 的具体挂接位置官方未给出示例，未自动化。不影响 su/root 主功能，只影响 KSU 管理器里的"模块卸载"。
2. **builtin 模式的按键 Safe Mode**：builtin 分支源码里未找到 `ksu_handle_input_handle_event` 的声明（`ksud.h` 仅有 `ksu_is_safe_mode()`），官方未给出手动挂接点，未自动化。防 bootloop 的兜底：出问题可用 fastboot 刷回原 boot.img（见下文）。
3. **builtin 分支在 4.4 的编译兼容性**：官方口径 4.14+、3.x 需回溯，4.4 居中；`kernel_compat.c` 的存在说明上游有兼容层。若首次编译失败，优先切换 `tiann-0.9.5` 模式（该路径所有 hook 均有官方文档背书）。
4. **boot.img 的 id 字段**：你现役 boot.img 的 id@576 与 AOSP 标准 `sha1(kernel+ramdisk+second)` 及常见变体均不匹配（疑似小米私有算法或历史重打包痕迹）；该字段 bootloader 不校验，`repack_boot.py` 按 AOSP 标准重算，不影响启动。

## 五、路线 A：GitHub Actions 云编译（推荐，免费）

1. 在 GitHub 新建一个空仓库（如 `sagit-ksu-kernel`）。
2. 把本套件整体推送为仓库根目录（保证 `repack_boot.py`、`ksu_hooks_patch.py`、`.github/workflows/build-kernel.yml` 在根目录下）。Windows 下可在套件目录里：
   ```powershell
   git init; git add .; git commit -m "sagit KSU build kit"; git remote add origin https://github.com/<你的用户名>/sagit-ksu-kernel.git; git push -u origin main
   ```
3. 打开仓库 → Actions → **build-sagit-kernel-ksu** → Run workflow：
   - `boot_img_url` 填你原 boot.img 的**直链**（任意可下载 URL）→ 编译完自动产出可直接刷写的 `boot_ksu.img`；
   - 不填则只产出 `Image.gz-dtb`，你用本地 `repack_boot.py` 自己装回。
   - 参数均可覆盖（换源、换分支、换 KSU 方式）。
4. 编译完成后在 Artifacts 下载 `sagit-ksu-kernel-<编号>`，内含：
   - `boot_ksu.img`（若提供了 base）
   - `Image.gz-dtb`、`kernel_config_ksu.txt`（编译配置，可复查 `CONFIG_KSU=y`）、`defconfig.orig`、`BUILD_INFO.txt`

## 六、路线 B：WSL 本地编译

管理员 PowerShell 安装 WSL：

```powershell
wsl --install -d Ubuntu-22.04
```

重启后进入 Ubuntu，把套件目录挂载进来执行（Windows 盘符在 WSL 里是 `/mnt/...`）：

```bash
cd /mnt/e/手机刷机包/小米6/MIUI14/ksu_build_kit
bash local_build.sh
# 想直接出 boot.img：
bash local_build.sh --boot-img "/mnt/e/手机刷机包/小米6/MIUI14/analysis/boot.img"
# 换 KSU 方式：
bash local_build.sh --ksu-type tiann-0.9.5
```

产物在 `kernel_src/out/Image.gz-dtb`（及可选 `boot_ksu.img`）。建议把 `--workdir` 设到 WSL 原生文件系统（如 `~/build`）以提升 IO 速度：`bash local_build.sh --workdir ~/build`。

> 也可以不用 WSL，直接用路线 A 的 Actions——同样的步骤、同样的脚本，且不占本地资源。

## 七、刷入与回滚

前提：已解锁 BL（小米6 解锁后 Fastboot 可用）。

```bash
# 刷入（boot_a/boot_b 同分区名，直接刷 boot 即可）
fastboot flash boot boot_ksu.img
fastboot reboot
```

- 首次开机后安装与内核匹配的管理器：
  - SukiSU builtin → [SukiSU-Ultra Releases](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases) 的 Manager APK
  - tiann-0.9.5 → KernelSU Manager v1.0.x 之前的版本（v0.9.5 配套）
- root 授权验证：管理器里能看到授权列表、`adb shell` 里 `su` 可用即成功。
- **回滚**：`fastboot flash boot 原boot.img`（保留好你的原包，本套件不会改动它）。
- tiann-0.9.5 模式内置 Safe Mode：开机后连按音量下可临时禁用 KSU（builtin 模式暂无此挂接点，见"需人工确认"）。

## 八、其他注意事项

- `repack_boot.py` 只替换 kernel 段：header、ramdisk、second 全部继承原包，页大小/对齐/id 字段按 header v0 规则处理。自测结果：同内核回填与原包逐字节一致（仅 id 的 20 字节按 AOSP 算法重算）；换不同尺寸 kernel 时 kernel/ramdisk 段定位与页对齐均验证正确。
- 内核版本串会变成 `4.4.253-perf-<新后缀>`：MIUI 对内核版本无强制校验，但部分 root 检测类模块会比对版本串，介意可在 defconfig 里改 `CONFIG_LOCALVERSION`。
- 首次刷入建议保留 TWRP/OrangeFox：本目录 `../rec/` 下已有 recovery 包，变砖可救。
- 本套件中的 KSU 集成方式、hook 签名、CONFIG 名均以"抓取当日"的官方文档/源码为准；上游若再次重构（如签名变更），`ksu_hooks_patch.py` 会在锚点失配时报错退出而不是静默产出无 root 内核——此时按报错信息核对最新官方文档即可。
