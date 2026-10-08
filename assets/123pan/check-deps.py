#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""123pan 原生版 (standard) deb 的依赖检查 / 生成工具。

上游 123panNextGen/123pan 用 Nuitka standalone 打包，release zip 里的
123pan.dist 自带 Python、PySide6/Qt6、PIL 等，但把一堆系统库留给了发行版
(libGL/libEGL、fontconfig、整套 libxcb、wayland、dbus 等)。所以原生版 deb
必须在 control 里写 Depends，否则在干净系统上跑不起来。

本脚本做的事:
  1. 解析 files 目录下所有 ELF 的 DT_NEEDED / DT_SONAME / VERNEED(纯 Python);
  2. 求出「包内没有提供、必须由系统提供」的 soname 集合;
  3. 用 SONAME_MAP 映射成 Debian 包名，按 depends / recommends 分级;
  4. 解析产物实际要求的最高 GLIBC / GLIBCXX / CXXABI 符号版本，生成
     libc6 (>= x.yy) 与 libstdc++6 (>= GCC 版本) 约束;
  5. 写入 (--write) 或校验 control 的 Depends / Recommends。

x64 与 arm64 两个构建的依赖集合并不相同(arm64 的 libQt6XcbQpa 需要
libICE/libSM 且不带 GLX，x64 的 QtNetwork 带 libgssapi_krb5 等)，所以按
各自产物生成，而不是写死一份全架构通用的清单。

用法:
    python3 check-deps.py --files <files 目录> --control <control 文件> [--write]

映射里的包名已对照 Debian bookworm(Deepin 23 基底) / trixie(Deepin 25 基底)
的 Contents-amd64/arm64 校验过；t64 更名用 "A | A-t64" 形式覆盖两代系统。
"""

import argparse
import os
import re
import struct
import sys
from collections import OrderedDict

# ELF64 / dynamic section 常量
SHT_DYNAMIC = 6
SHT_GNU_VERNEED = 0x6FFFFFFE
DT_NULL = 0
DT_NEEDED = 1
DT_SONAME = 14

# 提供 GLIBC_* 符号版本的 glibc 自带库 / 加载器
GLIBC_PROVIDERS = {
    "libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1",
    "libresolv.so.2", "libutil.so.1", "libanl.so.1", "libnss_files.so.2",
    "libthread_db.so.1", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1",
    "ld-linux-armhf.so.3", "ld-linux.so.2",
}
GLIBC_VER_RE = re.compile(r"^GLIBC_(\d+)\.(\d+)$")
STDCXX_VER_RE = re.compile(r"^(GLIBCXX|CXXABI)_(\d+)\.(\d+)(?:\.(\d+))?$")

# GLIBCXX_3.4.N / CXXABI_1.3.N -> 提供该版本的最低 libstdc++6(即 GCC)版本。
# 每一项都用对应发行版的 libstdc++6 deb 实测过 strings/VERDEF：
#   GLIBCXX_3.4.28=GCC10 29=GCC11 30=GCC12 31/32=GCC13.1 33=GCC14 34=GCC15
#   CXXABI_1.3.13=GCC11 14=GCC13.1 15=GCC14(GCC15 未新增)
# 上游 arm64 的 shiboken(PyPI 预编译 wheel)需要 GLIBCXX_3.4.32(GCC 13+)，
# 老 libstdc++6 上一启动就报 "GLIBCXX_3.4.32 not found"，所以必须按产物实际
# 需求生成版本约束，让 apt 安装期就升级/拒绝，而不是运行时崩。
# 产物需求高于表内最后一项时直接报错，防止上游换 wheel 后悄悄漏掉约束。
GLIBCXX_MIN_GCC = {
    (3, 4, 28): "10",
    (3, 4, 29): "11",
    (3, 4, 30): "12",
    (3, 4, 31): "13.1",
    (3, 4, 32): "13.1",
    (3, 4, 33): "14",
    (3, 4, 34): "15",
}
CXXABI_MIN_GCC = {
    (1, 3, 13): "11",
    (1, 3, 14): "13.1",
    (1, 3, 15): "14",
}

# libglib2.0-0 在 Debian 13/trixie 改名为 libglib2.0-0t64，用候选列表兼容两代
GLIB = ["libglib2.0-0", "libglib2.0-0t64"]
GTK3 = ["libgtk-3-0", "libgtk-3-0t64"]
ATK = ["libatk1.0-0", "libatk1.0-0t64"]
CUPS = ["libcups2", "libcups2t64"]

# soname -> (级别, 候选包名列表)
#   depends   : 程序启动 / 开窗 / 联网必需，缺失直接跑不起来
#   recommends: 只被可选 Qt 插件(打印、gtk3 主题、EGLFS)引用，缺了主程序也能用
SONAME_MAP = OrderedDict([
    # ---- libc / 编译运行时 ----
    ("libc.so.6",             ("depends", ["libc6"])),
    ("libm.so.6",             ("depends", ["libc6"])),
    ("libpthread.so.0",       ("depends", ["libc6"])),
    ("libdl.so.2",            ("depends", ["libc6"])),
    ("librt.so.1",            ("depends", ["libc6"])),
    ("libresolv.so.2",        ("depends", ["libc6"])),
    ("libutil.so.1",          ("depends", ["libc6"])),
    ("ld-linux-x86-64.so.2",  ("depends", ["libc6"])),
    ("ld-linux-aarch64.so.1", ("depends", ["libc6"])),
    ("ld-linux-armhf.so.3",   ("depends", ["libc6"])),
    ("libstdc++.so.6",        ("depends", ["libstdc++6"])),
    ("libgcc_s.so.1",         ("depends", ["libgcc-s1"])),
    ("libz.so.1",             ("depends", ["zlib1g"])),

    # ---- 图形 / 渲染 (libQt6Gui、PySide6 QtGui.so 直接链接) ----
    ("libGL.so.1",            ("depends", ["libgl1"])),
    ("libGLdispatch.so.0",    ("depends", ["libglvnd0"])),
    ("libGLX.so.0",           ("depends", ["libglx0"])),
    ("libOpenGL.so.0",        ("depends", ["libopengl0"])),
    ("libEGL.so.1",           ("depends", ["libegl1"])),
    ("libfontconfig.so.1",    ("depends", ["libfontconfig1"])),
    ("libfreetype.so.6",      ("depends", ["libfreetype6"])),

    # ---- GLib / DBus (libQt6Core / libQt6DBus / libQt6XcbQpa) ----
    ("libglib-2.0.so.0",      ("depends", GLIB)),
    ("libgthread-2.0.so.0",   ("depends", GLIB)),
    ("libgio-2.0.so.0",       ("depends", GLIB)),
    ("libgobject-2.0.so.0",   ("depends", GLIB)),
    ("libdbus-1.so.3",        ("depends", ["libdbus-1-3"])),

    # ---- X11 / xcb 平台插件 (libqxcb.so、libQt6XcbQpa.so.6) ----
    ("libX11.so.6",           ("depends", ["libx11-6"])),
    ("libX11-xcb.so.1",       ("depends", ["libx11-xcb1"])),
    ("libICE.so.6",           ("depends", ["libice6"])),
    ("libSM.so.6",            ("depends", ["libsm6"])),
    ("libxcb.so.1",           ("depends", ["libxcb1"])),
    ("libxcb-cursor.so.0",    ("depends", ["libxcb-cursor0"])),
    ("libxcb-icccm.so.4",     ("depends", ["libxcb-icccm4"])),
    ("libxcb-image.so.0",     ("depends", ["libxcb-image0"])),
    ("libxcb-keysyms.so.1",   ("depends", ["libxcb-keysyms1"])),
    ("libxcb-randr.so.0",     ("depends", ["libxcb-randr0"])),
    ("libxcb-render.so.0",     ("depends", ["libxcb-render0"])),
    ("libxcb-render-util.so.0", ("depends", ["libxcb-render-util0"])),
    ("libxcb-shape.so.0",     ("depends", ["libxcb-shape0"])),
    ("libxcb-shm.so.0",       ("depends", ["libxcb-shm0"])),
    ("libxcb-sync.so.1",      ("depends", ["libxcb-sync1"])),
    ("libxcb-util.so.1",      ("depends", ["libxcb-util1"])),
    ("libxcb-xfixes.so.0",    ("depends", ["libxcb-xfixes0"])),
    ("libxcb-xkb.so.1",       ("depends", ["libxcb-xkb1"])),
    ("libxcb-glx.so.0",       ("depends", ["libxcb-glx0"])),
    ("libxkbcommon.so.0",     ("depends", ["libxkbcommon0"])),
    ("libxkbcommon-x11.so.0", ("depends", ["libxkbcommon-x11-0"])),

    # ---- Wayland 平台插件 (libqwayland.so) ----
    ("libwayland-client.so.0", ("depends", ["libwayland-client0"])),
    ("libwayland-cursor.so.0", ("depends", ["libwayland-cursor0"])),
    ("libwayland-egl.so.1",   ("depends", ["libwayland-egl1"])),
    ("libwayland-server.so.0", ("depends", ["libwayland-server0"])),

    # ---- 网络 (libQt6Network.so.6) ----
    ("libbrotlidec.so.1",     ("depends", ["libbrotli1"])),
    ("libgssapi_krb5.so.2",   ("depends", ["libgssapi-krb5-2"])),

    # ---- 可选插件: 打印 (qt-plugins/printsupport) ----
    ("libcups.so.2",          ("recommends", CUPS)),

    # ---- 可选插件: gtk3 平台主题 (qt-plugins/platformthemes/libqgtk3.so) ----
    ("libgtk-3.so.0",         ("recommends", GTK3)),
    ("libgdk-3.so.0",         ("recommends", GTK3)),
    ("libatk-1.0.so.0",       ("recommends", ATK)),
    ("libcairo.so.2",         ("recommends", ["libcairo2"])),
    ("libcairo-gobject.so.2", ("recommends", ["libcairo-gobject2"])),
    ("libgdk_pixbuf-2.0.so.0", ("recommends", ["libgdk-pixbuf-2.0-0"])),
    ("libharfbuzz.so.0",      ("recommends", ["libharfbuzz0b"])),
    ("libpango-1.0.so.0",     ("recommends", ["libpango-1.0-0"])),
    ("libpangocairo-1.0.so.0", ("recommends", ["libpangocairo-1.0-0"])),

    # ---- 可选插件: EGLFS / linuxfb (egldeviceintegrations、platforms/libqlinuxfb) ----
    ("libdrm.so.2",           ("recommends", ["libdrm2"])),
    ("libgbm.so.1",           ("recommends", ["libgbm1"])),
])

# 已知、但故意不写进 Depends 的系统库
IGNORED_SONAMES = OrderedDict([
    ("libQt6EglFsKmsGbmSupport.so.6",
     "仅 eglfs/kiosk 平台插件需要；其提供者 libqt6opengl6 在 Debian arm64 上不存在，"
     "X11/Wayland 桌面不会加载该插件"),
])


def parse_elf(path):
    """返回 (needed, soname, {提供 GLIBC_* 的库: [版本号元组]})。仅支持 ELF64 LE。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 2:
        raise ValueError("unsupported ELF class: only ELF64 expected")
    if data[5] != 1:
        raise ValueError("unsupported ELF endianness: only little-endian expected")

    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    if e_shnum == 0 or e_shentsize < 64:
        return [], None, {}

    sections = []
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        (_, shtype, _flags, _addr, _off, _size, _link, _info,
         _align, _entsize) = struct.unpack_from("<IIQQQQIIQQ", data, base)
        name_off, = struct.unpack_from("<I", data, base)
        sections.append({"type": shtype, "off": _off, "size": _size,
                         "link": _link, "name_off": name_off,
                         "base": base})

    shstr = sections[e_shstrndx]
    strtab = data[shstr["off"]:shstr["off"] + shstr["size"]]

    def sec_name(sec):
        off = sec["name_off"]
        return strtab[off:strtab.find(b"\x00", off)]

    dynstr_sec = next((s for s in sections
                       if s["type"] == 3 and sec_name(s) == b".dynstr"), None)
    dynstr = (data[dynstr_sec["off"]:dynstr_sec["off"] + dynstr_sec["size"]]
              if dynstr_sec else b"")

    def cstr(blob, off):
        if off is None or off >= len(blob):
            return None
        end = blob.find(b"\x00", off)
        if end < 0:
            end = len(blob)
        return blob[off:end].decode("utf-8", "replace")

    # .dynamic -> DT_NEEDED / DT_SONAME（注意每条记录 2 x uint64 = 16 字节）
    needed, soname = [], None
    dyn_sec = next((s for s in sections if s["type"] == SHT_DYNAMIC), None)
    if dyn_sec:
        rec = struct.calcsize("<QQ")
        stop = dyn_sec["off"] + dyn_sec["size"]
        pos = dyn_sec["off"]
        while pos + rec <= min(stop, len(data)):
            tag, val = struct.unpack_from("<QQ", data, pos)
            pos += rec
            if tag == DT_NULL:
                break
            if tag == DT_NEEDED:
                name = cstr(dynstr, val)
                if name:
                    needed.append(name)
            elif tag == DT_SONAME:
                soname = cstr(dynstr, val)

    # SHT_GNU_verneed -> 该二进制要求的 GLIBC_* / GLIBCXX_* / CXXABI_* 符号版本
    glibc_versions = {}
    stdcxx_versions = {}
    ver_sec = next((s for s in sections if s["type"] == SHT_GNU_VERNEED), None)
    if ver_sec and ver_sec["link"] < len(sections):
        blob = data[ver_sec["off"]:ver_sec["off"] + ver_sec["size"]]
        link_str = data[sections[ver_sec["link"]]["off"]:
                        sections[ver_sec["link"]]["off"] +
                        sections[ver_sec["link"]]["size"]]
        vn_size = struct.calcsize("<HHIII")   # vn_version, vn_cnt, vn_file, vn_aux, vn_next
        va_size = struct.calcsize("<IHHII")   # vna_hash, vna_flags, vna_other, vna_name, vna_next
        pos = 0
        while pos + vn_size <= len(blob):
            _v, cnt, vn_file, vn_aux, vn_next = struct.unpack_from("<HHIII", blob, pos)
            fname = cstr(link_str, vn_file)
            apos = pos + vn_aux
            for _ in range(cnt):
                if apos + va_size > len(blob):
                    break
                _h, _fl, _oth, vna_name, vna_next = struct.unpack_from("<IHHII", blob, apos)
                vname = cstr(link_str, vna_name)
                if fname in GLIBC_PROVIDERS and vname:
                    m = GLIBC_VER_RE.match(vname)
                    if m:
                        glibc_versions.setdefault(fname, set()).add(
                            (int(m.group(1)), int(m.group(2))))
                if vname:
                    m = STDCXX_VER_RE.match(vname)
                    if m:
                        # 统一成三元组；只有两段的基线版本(如 GLIBCXX_3.4)
                        # 记为 x.y.0，当作"很老"的需求
                        stdcxx_versions.setdefault(m.group(1), set()).add(
                            (int(m.group(2)), int(m.group(3)),
                             int(m.group(4) or 0)))
                if not vna_next:
                    break
                apos += vna_next
            if not vn_next:
                break
            pos += vn_next

    return needed, soname, glibc_versions, stdcxx_versions


def scan_files(root):
    """遍历目录，返回 (缺失 soname->{来源}, 包内已提供 soname,
    GLIBC/GLIBCXX/CXXABI 最高版本, 明细)"""
    provided, missing, glibc_all, stdcxx_all, details = set(), {}, {}, {}, []
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "rb") as fh:
                    if fh.read(4) != b"\x7fELF":
                        continue
            except OSError as exc:
                print("WARN: cannot read %s: %s" % (path, exc), file=sys.stderr)
                continue
            try:
                needed, soname, glibc_versions, stdcxx_versions = parse_elf(path)
            except Exception as exc:          # 解析失败必须显式失败，不能悄悄漏检
                raise SystemExit("ERROR: 解析 ELF 失败 %s: %s" % (path, exc))
            rel = os.path.relpath(path, root)
            provided.add(fn)
            if soname:
                provided.add(soname)
            for lib, vers in glibc_versions.items():
                glibc_all.setdefault(lib, set()).update(vers)
            for kind, vers in stdcxx_versions.items():
                stdcxx_all.setdefault(kind, set()).update(vers)
            if needed:
                details.append((rel, needed))
            for son in needed:
                missing.setdefault(son, []).append(rel)

    max_glibc = None
    for vers in glibc_all.values():
        for v in vers:
            if max_glibc is None or v > max_glibc:
                max_glibc = v
    max_glibcxx = max(stdcxx_all.get("GLIBCXX") or [], default=None)
    max_cxxabi = max(stdcxx_all.get("CXXABI") or [], default=None)
    unresolved = {k: v for k, v in missing.items() if k not in provided}
    return (unresolved, provided, max_glibc, max_glibcxx, max_cxxabi, details)


def parse_control(path):
    """按 RFC822 规则解析 control，返回字段名 -> 值(含续行)。"""
    fields = OrderedDict()
    current = None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith((" ", "\t")):
                if current:
                    fields[current] += "\n" + line
                continue
            if not line.strip():
                continue
            if ":" in line:
                current = line.split(":", 1)[0]
                fields[current] = line.split(":", 1)[1].strip()
    return fields


def dep_packages(value):
    """把 Depends/Recommends 值解析成包名集合(忽略版本约束与 alternatives)。"""
    names = set()
    if not value:
        return names
    for rel in value.replace("\n", " ").split(","):
        for alt in rel.split("|"):
            alt = re.sub(r"\(.*?\)", "", alt)
            alt = alt.strip()
            if alt:
                names.add(alt)
    return names


def format_field(name, packages, width=78):
    """生成带续行的 control 字段：relation(含 | alternatives) 内不换行，逗号挂在行尾。"""
    if not packages:
        return "%s: (none)\n" % name
    header = name + ":"
    lines, cur = [], header
    for i, rel in enumerate(packages):
        item = rel + ("," if i < len(packages) - 1 else "")
        if cur != header and len(cur) + 1 + len(item) > width:
            lines.append(cur)
            cur = " " + item              # 续行以空格开头
        else:
            cur = cur + " " + item
    lines.append(cur)
    return "\n".join(lines) + "\n"


def write_control(path, depends, recommends):
    """移除旧的 Depends/Recommends 并插到 Description 之前。"""
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    out, skip = [], False
    for line in lines:
        if line.startswith((" ", "\t")):
            if skip:
                continue
            out.append(line)
            continue
        key = line.split(":", 1)[0]
        if key in ("Depends", "Recommends"):
            skip = True
            continue
        skip = False
        out.append(line)

    block = (format_field("Depends", depends) +
             format_field("Recommends", recommends))
    idx = next((i for i, l in enumerate(out) if l.startswith("Description:")), None)
    if idx is None:
        out.append("\n" + block)
    else:
        out[idx:idx] = [block]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成/校验 123pan standard deb 的 Depends")
    ap.add_argument("--files", required=True, help="deb 内 /opt/apps/<id>/files 目录")
    ap.add_argument("--control", required=True, help="DEBIAN/control 路径")
    ap.add_argument("--write", action="store_true", help="把结果写回 control")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.files):
        raise SystemExit("ERROR: --files 目录不存在: %s" % args.files)
    if not os.path.isfile(args.control):
        raise SystemExit("ERROR: control 不存在: %s" % args.control)

    unresolved, _provided, max_glibc, max_glibcxx, max_cxxabi, details = \
        scan_files(args.files)

    errors = []
    # 1) 未映射的系统库 -> 上游新增了依赖，需要先补 SONAME_MAP
    unmapped = sorted(s for s in unresolved
                      if s not in IGNORED_SONAMES and s not in SONAME_MAP)
    if unmapped:
        for son in unmapped:
            who = ", ".join(unresolved[son][:3])
            errors.append("soname 未在 SONAME_MAP 中登记: %s <- %s" % (son, who))

    # 2) 生成期望的 Depends / Recommends（按 relation 字符串去重，
    #    同一 relation 内的 "A | A-t64" 是 alternatives，不能拆成逗号并列）
    depends, recommends = [], []
    for son in sorted(unresolved):
        if son in IGNORED_SONAMES or son not in SONAME_MAP:
            continue
        level, pkgs = SONAME_MAP[son]
        relation = " | ".join(pkgs)
        bucket = depends if level == "depends" else recommends
        if relation not in bucket:
            bucket.append(relation)

    # 3) glibc 版本约束
    if max_glibc is None:
        errors.append("未能从产物解析出 GLIBC_* 版本要求，无法生成 libc6 版本约束")
        glibc_str = None
    else:
        glibc_str = "%d.%d" % max_glibc

    # 4) libstdc++6 版本约束（由 GLIBCXX/CXXABI 符号版本反查最低 GCC）
    libstdcxx_str = None
    if "libstdc++.so.6" in unresolved:
        if max_glibcxx is None and max_cxxabi is None:
            errors.append("未从产物解析出 GLIBCXX/CXXABI 版本要求，"
                          "无法生成 libstdc++6 版本约束")
        else:
            picks = []
            for label, mx, table in (("GLIBCXX", max_glibcxx, GLIBCXX_MIN_GCC),
                                     ("CXXABI", max_cxxabi, CXXABI_MIN_GCC)):
                if mx is None:
                    continue
                pick = next((v for k, v in sorted(table.items())
                             if mx <= k), None)
                if pick is None:
                    errors.append(
                        "产物要求 %s %s 超出映射表，请更新 "
                        "GLIBCXX_MIN_GCC/CXXABI_MIN_GCC"
                        % (label, ".".join(str(x) for x in mx)))
                    continue
                picks.append(pick)
            if picks:
                libstdcxx_str = max(
                    picks, key=lambda s: tuple(int(x) for x in s.split(".")))

    # 去重并按字母序稳定输出
    depends = sorted(set(depends))
    recommends = sorted(set(recommends))
    if glibc_str:
        depends = ["libc6 (>= %s)" % glibc_str] + [p for p in depends
                                                   if not p.startswith("libc6")]
    if libstdcxx_str:
        # 就地把普通条目换成版本化条目，保持字母序位置不变
        depends = [("libstdc++6 (>= %s)" % libstdcxx_str)
                   if p == "libstdc++6" else p for p in depends]

    # 先打印摘要，再统一报告错误，避免 stdout/stderr 交错
    print("== 123pan 依赖检查 ==")
    print("扫描 ELF: %d, 系统需提供 soname: %d (忽略 %d)"
          % (len(details), len(unresolved), len(IGNORED_SONAMES)))
    print("产物要求最高 glibc 符号版本: %s" % (glibc_str or "未知"))
    print("libstdc++6 版本约束(GLIBCXX/CXXABI 推导): %s" % (libstdcxx_str or "-"))
    for son in sorted(unresolved):
        if son in IGNORED_SONAMES:
            print("  [跳过] %-32s %s" % (son, IGNORED_SONAMES[son]))
    print("Depends   : %s" % ", ".join(depends))
    print("Recommends: %s" % (", ".join(recommends) or "-"))
    sys.stdout.flush()

    def fail():
        for e in errors:
            print("ERROR: " + e, file=sys.stderr)
        raise SystemExit(1)

    # 有前序错误(未映射 soname / 版本约束解析失败)时：不回写，也不回读校验
    if errors:
        fail()

    if args.write:
        write_control(args.control, depends, recommends)
        print("已写入 control: %d 个 Depends, %d 个 Recommends"
              % (len(depends), len(recommends)))
        sys.stdout.flush()

    # 5) 回读 control，逐条校验与产物一致
    fields = parse_control(args.control)
    have_dep = dep_packages(fields.get("Depends"))
    have_rec = dep_packages(fields.get("Recommends")) | have_dep

    for son in sorted(unresolved):
        if son in IGNORED_SONAMES:
            continue
        if son not in SONAME_MAP:
            continue                    # 上面已作为 unmapped 报错
        level, pkgs = SONAME_MAP[son]
        target = have_dep if level == "depends" else have_rec
        if not set(pkgs) & target:
            errors.append("%s 需要包 %s，但 control 的 %s 里没有"
                          % (son, " | ".join(pkgs),
                             "Depends" if level == "depends" else "Depends/Recommends"))

    declared = fields.get("Depends", "")
    m = re.search(r"libc6\s*\(\s*>=\s*(\d+\.\d+)\s*\)", declared)
    if glibc_str:
        if not m:
            errors.append("Depends 缺少 libc6 (>= %s) 版本约束" % glibc_str)
        elif tuple(int(x) for x in m.group(1).split(".")) != max_glibc:
            errors.append("libc6 约束 (%s) 与产物实际要求 (%s) 不一致"
                          % (m.group(1), glibc_str))
    m = re.search(r"libstdc\+\+6\s*\(\s*>=\s*([\d.]+)\s*\)", declared)
    if libstdcxx_str:
        if not m:
            errors.append("Depends 缺少 libstdc++6 (>= %s) 版本约束"
                          % libstdcxx_str)
        elif m.group(1) != libstdcxx_str:
            errors.append("libstdc++6 约束 (%s) 与产物实际要求 (%s) 不一致"
                          % (m.group(1), libstdcxx_str))
    if not declared.strip():
        errors.append("control 的 Depends 为空")

    if errors:
        fail()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
