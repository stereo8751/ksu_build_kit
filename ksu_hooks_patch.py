#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ksu_hooks_patch.py —— KernelSU/SukiSU 非 GKI 手动集成：向 4.4 内核源码插入 hook 调用

依据（全部为实际抓取的官方文档/源码，非凭记忆编写）：
  1. KernelSU 官方非 GKI 手动集成文档（存档版，覆盖 4.8 以下内核）：
     https://kernelsu.org/guide/how-to-integrate-for-non-gki.html
  2. SukiSU-Ultra 官方集成文档（builtin 分支为无 kprobe 的手动钩子设计）：
     https://github.com/SukiSU-Ultra/SukiSU-Ultra/blob/main/docs/guide/how-to-integrate.md
  3. SukiSU-Ultra builtin 分支 kernel/feature/sucompat.h / sucompat.c / kernel_umount.h
     （钩子函数签名逐一核对过，注意：非 SUSFS 构建走 `const char __user **` 签名，
       SUSFS 构建走 `struct filename **` 签名；4.4 内核没有 THREAD_INFO_IN_TASK，
       CONFIG_KSU_SUSFS 依赖它而自动不可用，因此这里一律按非 SUSFS 签名打补丁）

模式：
  sukisu-builtin  SukiSU-Ultra builtin 分支（推荐，见 README"fork 选择"）
  tiann-0.9.5     tiann/KernelSU v0.9.5（非 GKI 最后官方支持版，回退方案）

关键事实（sagit 4.4.253 / msm8998）：
  - fs/exec.c 里有 do_execveat_common（4.4 起 execve/execveat 共用此函数）
  - fs/open.c 没有 do_faccessat（4.5 才引入），faccessat 直接是 SYSCALL_DEFINE3
  - fs/stat.c 没有 vfs_statx（4.11 才引入），用 vfs_fstatat 挂 ksu_handle_stat
  - 以上三点与官方文档对"4.4 老内核"的指引一致

用法：
  python3 ksu_hooks_patch.py --mode sukisu-builtin --src /path/to/kernel
  python3 ksu_hooks_patch.py --mode tiann-0.9.5   --src /path/to/kernel

脚本幂等：已打过补丁的文件会跳过；核心锚点缺失时报错退出（防止 CI 静默产出无 root 内核）。
"""

import argparse
import sys

INDENT = "\t"

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

class PatchError(Exception):
    pass


def load(path):
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def save(path, text):
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)
    print("  [补丁] %s 已修改" % path)


def already_patched(text):
    return "#ifdef CONFIG_KSU" in text


def find_anchor_line(text, anchor, path, required=True):
    """返回包含 anchor 的行号（0 起）。required=False 时找不到返回 None"""
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if anchor in ln:
            return i
    if required:
        raise PatchError("%s：找不到锚点 %r（内核源码结构与预期不符，需人工确认）"
                         % (path, anchor))
    return None


def insert_before(text, anchor, block, path):
    """在包含 anchor 的那一行之前插入 block（幂等判断由各 patch_* 入口负责）"""
    i = find_anchor_line(text, anchor, path)
    lines = text.splitlines(keepends=True)
    lines.insert(i, block)
    return "".join(lines)


def insert_after_body(text, anchor, block, path):
    """在 anchor 所在函数体的 `{` 之后插入 block（幂等判断由各 patch_* 入口负责）"""
    i = find_anchor_line(text, anchor, path)
    lines = text.splitlines(keepends=True)
    # 从 anchor 行向后找第一个独占一行的 `{`
    j = i
    while j < len(lines) and "{" not in lines[j]:
        j += 1
    if j >= len(lines):
        raise PatchError("%s：锚点 %r 之后找不到函数体起始 `{`" % (path, anchor))
    lines.insert(j + 1, block)
    return "".join(lines)


def insert_before_comment(text, marker, block, path):
    """在包含 marker 的注释块（其前面的 `/*`）之前插入 block；已存在则返回 None 表示跳过"""
    if "path_umount(struct path *path, int flags)" in text:
        print("  [跳过] %s 已含 path_umount 回溯" % path)
        return None
    idx = text.find(marker)
    if idx == -1:
        raise PatchError("%s：找不到锚点注释 %r" % (path, marker))
    cstart = text.rfind("/*", 0, idx)
    if cstart == -1:
        raise PatchError("%s：锚点注释前找不到 /* 起始" % path)
    return text[:cstart] + block + text[cstart:]


def require_all(paths):
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="surrogateescape"):
                pass
        except OSError:
            raise PatchError("文件不存在：%s（内核源码树不完整？）" % p)


# ---------------------------------------------------------------------------
# 补丁块（签名均与 builtin 分支 sucompat.c 非 SUSFS 实现 / KernelSU 官方文档逐字核对）
# ---------------------------------------------------------------------------

# fs/exec.c：do_execveat_common 是 4.4 中 execve/execveat 的公共路径。
# builtin 分支直接调用封装好的 ksu_handle_execveat（内部自动分流 ksud/sucompat）。
EXEC_DECL_SUKISU = (
    "#ifdef CONFIG_KSU\n"
    "extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr, void *argv,\n"
    "\t\t\tvoid *envp, int *flags);\n"
    "#endif\n"
)
EXEC_DECL_TIANN = (
    "#ifdef CONFIG_KSU\n"
    "extern bool ksu_execveat_hook __read_mostly;\n"
    "extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr, void *argv,\n"
    "\t\t\tvoid *envp, int *flags);\n"
    "extern int ksu_handle_execveat_sucompat(int *fd, struct filename **filename_ptr,\n"
    "\t\t\t\t void *argv, void *envp, int *flags);\n"
    "#endif\n"
)
EXEC_CALL_SUKISU = (
    "#ifdef CONFIG_KSU\n"
    "\tksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);\n"
    "#endif\n"
)
EXEC_CALL_TIANN = (
    "#ifdef CONFIG_KSU\n"
    "\tif (unlikely(ksu_execveat_hook))\n"
    "\t\tksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);\n"
    "\telse\n"
    "\t\tksu_handle_execveat_sucompat(&fd, &filename, &argv, &envp, &flags);\n"
    "#endif\n"
)

# fs/open.c：4.4 没有 do_faccessat，直接挂在 faccessat 系统调用入口。
# builtin 非 SUSFS 签名：ksu_handle_faccessat(int *dfd, const char __user **filename_user,
#                                             int *mode, int *__unused_flags)
OPEN_DECL = (
    "#ifdef CONFIG_KSU\n"
    "extern int ksu_handle_faccessat(int *dfd, const char __user **filename_user, int *mode,\n"
    "\t\t\t int *flags);\n"
    "#endif\n"
)
OPEN_CALL = (
    "#ifdef CONFIG_KSU\n"
    "\tksu_handle_faccessat(&dfd, &filename, &mode, NULL);\n"
    "#endif\n"
)

# fs/stat.c：4.4 没有 vfs_statx，用 vfs_fstatat（newfstatat/stat/lstat 底层）。
# builtin 非 SUSFS 签名：ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags)
STAT_DECL = (
    "#ifdef CONFIG_KSU\n"
    "extern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\n"
    "#endif\n"
)
STAT_CALL = (
    "#ifdef CONFIG_KSU\n"
    "\tksu_handle_stat(&dfd, &filename, &flag);\n"
    "#endif\n"
)

# fs/read_write.c：仅 tiann-0.9.5 模式（官方文档明确要求的手动集成点）。
READ_DECL = (
    "#ifdef CONFIG_KSU\n"
    "extern bool ksu_vfs_read_hook __read_mostly;\n"
    "extern int ksu_handle_vfs_read(struct file **file_ptr, char __user **buf_ptr,\n"
    "\t\t\tsize_t *count_ptr, loff_t **pos);\n"
    "#endif\n"
)
READ_CALL = (
    "#ifdef CONFIG_KSU\n"
    "\tif (unlikely(ksu_vfs_read_hook))\n"
    "\t\tksu_handle_vfs_read(&file, &buf, &count, &pos);\n"
    "#endif\n"
)

# drivers/input/input.c：仅 tiann-0.9.5 模式，音量键三连击进 Safe Mode（防 bootloop）。
INPUT_DECL = (
    "#ifdef CONFIG_KSU\n"
    "extern bool ksu_input_hook __read_mostly;\n"
    "extern int ksu_handle_input_handle_event(unsigned int *type, unsigned int *code, int *value);\n"
    "#endif\n"
)
INPUT_CALL = (
    "#ifdef CONFIG_KSU\n"
    "\tif (unlikely(ksu_input_hook))\n"
    "\t\tksu_handle_input_handle_event(&type, &code, &value);\n"
    "#endif\n"
)

# fs/namespace.c：path_umount 从 5.9 回溯（官方文档做法），
# "卸载模块"功能依赖它。4.4 没有 MNT_LOCKED（4.13 才引入），用 #ifdef 兜底。
UMOUNT_BACKPORT = (
    "/* ==== KernelSU: path_umount 回溯（官方非 GKI 文档做法；4.4 无 MNT_LOCKED，#ifdef 兜底） ==== */\n"
    "static int can_umount(const struct path *path, int flags)\n"
    "{\n"
    "\tstruct mount *mnt = real_mount(path->mnt);\n"
    "\n"
    "\tif (flags & ~(MNT_FORCE | MNT_DETACH | MNT_EXPIRE | UMOUNT_NOFOLLOW))\n"
    "\t\treturn -EINVAL;\n"
    "\tif (!may_mount())\n"
    "\t\treturn -EPERM;\n"
    "\tif (path->dentry != path->mnt->mnt_root)\n"
    "\t\treturn -EINVAL;\n"
    "\tif (!check_mnt(mnt))\n"
    "\t\treturn -EINVAL;\n"
    "#ifdef MNT_LOCKED\n"
    "\tif (mnt->mnt.mnt_flags & MNT_LOCKED) /* Check optimistically */\n"
    "\t\treturn -EINVAL;\n"
    "#endif\n"
    "\tif (flags & MNT_FORCE && !capable(CAP_SYS_ADMIN))\n"
    "\t\treturn -EPERM;\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "int path_umount(struct path *path, int flags)\n"
    "{\n"
    "\tstruct mount *mnt = real_mount(path->mnt);\n"
    "\tint ret;\n"
    "\n"
    "\tret = can_umount(path, flags);\n"
    "\tif (!ret)\n"
    "\t\tret = do_umount(mnt, flags);\n"
    "\n"
    "\t/* we mustn't call path_put() as that would clear mnt_expiry_mark */\n"
    "\tdput(path->dentry);\n"
    "\tmntput_no_expire(mnt);\n"
    "\treturn ret;\n"
    "}\n"
    "/* ==== KernelSU 回溯结束 ==== */\n"
)

UMOUNT_MARKER = "Now umount can handle mount points as well as block devices"


# ---------------------------------------------------------------------------
# 各模式补丁流程
# ---------------------------------------------------------------------------

def patch_exec(src, mode):
    path = src + "/fs/exec.c"
    print("-> fs/exec.c")
    text = load(path)
    if already_patched(text):
        print("  [跳过] 已含 CONFIG_KSU 补丁")
        return
    decl = EXEC_DECL_TIANN if mode == "tiann-0.9.5" else EXEC_DECL_SUKISU
    call = EXEC_CALL_TIANN if mode == "tiann-0.9.5" else EXEC_CALL_SUKISU
    # 4.4 的公共执行路径是 do_execveat_common；老分支若叫 do_execve_common 也能命中。
    # 注意：sagit-p-oss(4.4.153) 的 do_execveat_common 是老式内联实现（无 __do_execve_file
    # 调用，已在真实源码上验证），因此 hook 调用统一插在函数体入口 `{` 之后——
    # 与官方 kprobe 版挂接在 do_execveat_common 入口的位置语义一致。
    anchor = None
    for cand in ("static int do_execveat_common(", "static int do_execve_common("):
        if find_anchor_line(text, cand, path, required=False) is not None:
            anchor = cand
            break
    if anchor is None:
        raise PatchError("fs/exec.c：找不到 do_execveat_common（4.4 应存在），需人工确认挂接点")
    text = insert_before(text, anchor, decl, path)
    text = insert_after_body(text, anchor, call, path)
    save(path, text)


def patch_open(src):
    path = src + "/fs/open.c"
    print("-> fs/open.c")
    text = load(path)
    if already_patched(text):
        print("  [跳过] 已含 CONFIG_KSU 补丁")
        return
    # 4.4 没有 do_faccessat，faccessat 是 SYSCALL_DEFINE3（官方文档对 <4.17 内核的指引）
    if find_anchor_line(text, "SYSCALL_DEFINE3(faccessat", path, required=False) is not None:
        anchor = "SYSCALL_DEFINE3(faccessat"
    elif find_anchor_line(text, "long do_faccessat", path, required=False) is not None:
        anchor = "long do_faccessat"  # 兼容 4.5+ 的 do_faccessat
    else:
        raise PatchError("fs/open.c：找不到 faccessat 挂接点，需人工确认")
    text = insert_before(text, anchor, OPEN_DECL, path)
    text = insert_after_body(text, anchor, OPEN_CALL, path)
    save(path, text)


def patch_stat(src):
    path = src + "/fs/stat.c"
    print("-> fs/stat.c")
    text = load(path)
    if already_patched(text):
        print("  [跳过] 已含 CONFIG_KSU 补丁")
        return
    if find_anchor_line(text, "int vfs_fstatat(", path, required=False) is not None:
        anchor = "int vfs_fstatat("
    elif find_anchor_line(text, "int vfs_statx(", path, required=False) is not None:
        # 4.11+ 才有 vfs_statx；sagit 4.4 走不到这里，仅为通用性保留
        raise PatchError("fs/stat.c：该内核有 vfs_statx，请人工改用 vfs_statx 挂接（签名不同）")
    else:
        raise PatchError("fs/stat.c：找不到 vfs_fstatat 挂接点，需人工确认")
    text = insert_before(text, anchor, STAT_DECL, path)
    text = insert_after_body(text, anchor, STAT_CALL, path)
    save(path, text)


def patch_read(src):
    path = src + "/fs/read_write.c"
    print("-> fs/read_write.c（仅 tiann-0.9.5 模式）")
    text = load(path)
    if already_patched(text):
        print("  [跳过] 已含 CONFIG_KSU 补丁")
        return
    text = insert_before(text, "ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)",
                         READ_DECL, path)
    text = insert_after_body(text, "ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)",
                             READ_CALL, path)
    save(path, text)


def patch_input(src):
    path = src + "/drivers/input/input.c"
    print("-> drivers/input/input.c（仅 tiann-0.9.5 模式：Safe Mode）")
    text = load(path)
    if already_patched(text):
        print("  [跳过] 已含 CONFIG_KSU 补丁")
        return
    text = insert_before(text, "static void input_handle_event(struct input_dev *dev,",
                         INPUT_DECL, path)
    text = insert_after_body(text, "static void input_handle_event(struct input_dev *dev,",
                             INPUT_CALL, path)
    save(path, text)


def patch_namespace(src):
    path = src + "/fs/namespace.c"
    print("-> fs/namespace.c（path_umount 回溯，两种模式都需要）")
    text = load(path)
    patched = insert_before_comment(text, UMOUNT_MARKER, UMOUNT_BACKPORT, path)
    if patched is not None:
        save(path, patched)


def main():
    ap = argparse.ArgumentParser(description="KernelSU/SukiSU 非 GKI 手动集成 hook 补丁")
    ap.add_argument("--mode", required=True, choices=["sukisu-builtin", "tiann-0.9.5"],
                    help="集成方式（与 setup.sh 拉取的分支对应）")
    ap.add_argument("--src", required=True, help="内核源码根目录")
    args = ap.parse_args()

    src = args.src.rstrip("/")
    print("=== KernelSU 手动集成 hook 补丁（mode=%s）===" % args.mode)

    # 先跑 path_umount 回溯（fs/namespace.c 无 #ifdef CONFIG_KSU 标记，单独判重）
    try:
        patch_namespace(src)
    except PatchError as e:
        # path_umount 只影响"卸载模块"功能，缺失不致命：警告但不阻断
        print("  [警告] %s（该功能降级，不阻断编译）" % e)

    patch_exec(src, args.mode)
    patch_open(src)
    patch_stat(src)
    if args.mode == "tiann-0.9.5":
        patch_read(src)
        patch_input(src)

    print("=== 补丁完成 ===")
    print("提醒（需人工确认的部分，见 README）：")
    print("  - SukiSU builtin 的 ksu_handle_umount 挂到 setresuid 的挂接点未自动化，"
          "签名 int ksu_handle_umount(uid_t old_uid, uid_t new_uid)（kernel_umount.h 已核对）")
    print("  - SukiSU builtin 的输入按键 Safe Mode 挂接点未在官方文档中给出，未自动化")


if __name__ == "__main__":
    try:
        main()
    except PatchError as e:
        print("[ksu_hooks_patch] 失败：%s" % e)
        sys.exit(1)
