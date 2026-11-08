#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repack_boot.py —— 把新编译的 Image.gz-dtb 装回 boot.img（只替换 kernel 段）

适用：Android boot image header v0（小米6/MIUI14 的 boot.img 即此格式，page_size=4096）。

boot_img_hdr v0 布局（均为小端 u32，除非另注）：
  偏移   0   magic   "ANDROID!"（8 字节）
  偏移   8   kernel_size
  偏移  12   kernel_addr
  偏移  16   ramdisk_size
  偏移  20   ramdisk_addr
  偏移  24   second_size
  偏移  28   second_addr
  偏移  32   tags_addr
  偏移  36   page_size
  偏移  40   header_version
  偏移  44   os_version
  偏移  48   name[16]
  偏移  64   cmdline[512]
  偏移 576   id[8]（前 20 字节 = sha1(kernel+ramdisk+second)，后 12 字节为 0）
  偏移 608   extra_cmdline[1024]

分区起始位置（AOSP mkbootimg 规则）：
  kernel  = page_size
  ramdisk = page_align(page_size + kernel_size)
  second  = page_align(ramdisk 结束位置)

用法：
  python repack_boot.py --kernel Image.gz-dtb --base boot.img --out boot_ksu.img
  python repack_boot.py --kernel Image.gz-dtb --base boot.img --out boot_ksu.img --ramdisk newramdisk.cpio.gz
"""

import argparse
import hashlib
import struct
import sys

MAGIC = b"ANDROID!"

# header v0 各字段偏移（字节）
OFF_KERNEL_SIZE = 8
OFF_RAMDISK_SIZE = 16
OFF_SECOND_SIZE = 24
OFF_PAGE_SIZE = 36
OFF_HEADER_VERSION = 40
OFF_ID = 576          # id[8]，共 32 字节，前 20 字节是 sha1
ID_SHA1_LEN = 20


def die(msg):
    print("[repack] 错误：%s" % msg)
    sys.exit(1)


def page_align(x, page):
    """按 boot 分区页大小向上对齐"""
    return (x + page - 1) & ~(page - 1)


def parse_header(hdr):
    """解析并基础校验 header 页（hdr 必须是完整一页）"""
    if hdr[:8] != MAGIC:
        die("base boot.img 魔数不是 ANDROID!（got %r）" % hdr[:8])
    kernel_size, _kaddr, ramdisk_size, _raddr, second_size, _saddr = struct.unpack_from(
        "<6I", hdr, 8)
    page_size = struct.unpack_from("<I", hdr, OFF_PAGE_SIZE)[0]
    header_version = struct.unpack_from("<I", hdr, OFF_HEADER_VERSION)[0]
    if page_size == 0 or (page_size & (page_size - 1)) != 0:
        die("page_size 非法（%d），必须是 2 的幂" % page_size)
    if header_version != 0:
        die("仅支持 header v0，当前 header_version=%d" % header_version)
    if kernel_size == 0:
        die("base 的 kernel_size 为 0，疑似损坏的 boot.img")
    return {
        "kernel_size": kernel_size,
        "ramdisk_size": ramdisk_size,
        "second_size": second_size,
        "page_size": page_size,
    }


def read_sections(base, info):
    """从 base boot.img 里切出 kernel/ramdisk/second 三段原始数据"""
    page = info["page_size"]
    off_kernel = page
    off_ramdisk = page_align(off_kernel + info["kernel_size"], page)
    off_second = page_align(off_ramdisk + info["ramdisk_size"], page)
    if len(base) < off_second + info["second_size"]:
        die("base 文件比 header 声明的布局小，疑似损坏")
    kernel = base[off_kernel:off_kernel + info["kernel_size"]]
    ramdisk = base[off_ramdisk:off_ramdisk + info["ramdisk_size"]]
    second = base[off_second:off_second + info["second_size"]]
    return kernel, ramdisk, second


def main():
    ap = argparse.ArgumentParser(description="替换 boot.img 的 kernel 段并重打包（header v0）")
    ap.add_argument("--kernel", required=True, help="新内核文件（Image.gz-dtb）")
    ap.add_argument("--base", required=True, help="原 boot.img（提供 header/ramdisk/second）")
    ap.add_argument("--out", required=True, help="输出 boot.img 路径")
    ap.add_argument("--ramdisk", default=None, help="可选：用新的 ramdisk 覆盖 base 里的 ramdisk")
    args = ap.parse_args()

    base = open(args.base, "rb").read()
    info = parse_header(base)
    kernel, ramdisk, second = read_sections(base, info)

    if args.ramdisk:
        ramdisk = open(args.ramdisk, "rb").read()
        print("[repack] 使用外部 ramdisk：%s（%d 字节）" % (args.ramdisk, len(ramdisk)))

    new_kernel = open(args.kernel, "rb").read()
    if len(new_kernel) == 0:
        die("--kernel 文件为空")
    # 新 kernel 必须包含 gzip 魔数（Image.gz-dtb 以 1f 8b 开头）
    if new_kernel[:2] != b"\x1f\x8b":
        print("[repack] 警告：新 kernel 开头不是 gzip 魔数（1f 8b），请确认传的是 Image.gz-dtb")

    page = info["page_size"]

    # 重新计算 sha1 id：AOSP 规则 sha1(kernel + ramdisk + second)，取前 20 字节
    sha1 = hashlib.sha1(new_kernel + ramdisk + second).digest()[:ID_SHA1_LEN]

    # 复制原 header 并更新字段
    header = bytearray(base[:page])
    struct.pack_into("<I", header, OFF_KERNEL_SIZE, len(new_kernel))
    struct.pack_into("<I", header, OFF_RAMDISK_SIZE, len(ramdisk))
    header[OFF_ID:OFF_ID + ID_SHA1_LEN] = sha1

    # 组装：header 页 | kernel 按页对齐 | ramdisk 按页对齐 | second 按页对齐
    out = bytearray(header)
    out += new_kernel
    out += b"\x00" * (page_align(len(new_kernel), page) - len(new_kernel))
    out += ramdisk
    out += b"\x00" * (page_align(len(ramdisk), page) - len(ramdisk))
    out += second
    out += b"\x00" * (page_align(len(second), page) - len(second))

    with open(args.out, "wb") as f:
        f.write(out)

    # 输出摘要 + 自检
    ok_magic = out[:8] == MAGIC
    ok_header = struct.unpack_from("<I", out, OFF_HEADER_VERSION)[0] == 0
    ok_size = len(out) == page + page_align(len(new_kernel), page) \
        + page_align(len(ramdisk), page) + page_align(len(second), page)
    print("[repack] 输出：%s（%d 字节）" % (args.out, len(out)))
    print("[repack] kernel: %d -> %d 字节 | ramdisk: %d 字节 | second: %d 字节 | page: %d"
          % (info["kernel_size"], len(new_kernel), len(ramdisk), len(second), page))
    print("[repack] 新 sha1(id): %s" % sha1.hex())
    print("[repack] 自检：magic=%s header_version=%s 布局=%s"
          % ("OK" if ok_magic else "FAIL", "OK" if ok_header else "FAIL",
             "OK" if ok_size else "FAIL"))
    if not (ok_magic and ok_header and ok_size):
        die("自检失败")


if __name__ == "__main__":
    main()
