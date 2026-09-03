#!/usr/bin/env python3
"""校验 serve.py 双副本的「安全加固」保持一致。

背景（第八批验收发现，原 BUG-44 / BUG-45）：
    `scripts/serve.py` 是开发/CI 副本；Cordis 插件实际运行的是
    `plugins/dsh-self-evolving-agent/scripts/serve.py`（生产副本，单一真源）。

    两副本**允许**在引导/路径发现代码上不同（生产副本多了
    `_discover_project_root` / `_walk_up`），但**安全加固必须一致**——
    `scripts/serve.py` 的 docstring 明确要求：修改本文件的安全逻辑时须同步到
    插件副本，否则加固不会作用于生产路径。

    第八批曾出现：BUG-44/45 加固只落到开发副本，生产副本仍是 8/29 原版，
    导致「恰好 limit 字节的合法行被误拒」「batch_size 可传入负数/超大值」。
    本脚本用于在 CI / 发布前拦下这类漂移。

校验对象（安全加固相关的方法/函数体，按缩进截取后做空白归一比较）：
    - `_iter_limited_lines`   BUG-44：先剥离行尾 `\\n` 再测负载长度（off-by-one）
    - `stats`                 BUG-45：categories 须 `sorted(...)` 保证输出确定性
    - `planner_plan`          BUG-45：batch_size 服务端类型/范围校验

用法：
    python scripts/check_serve_sync.py            # 仅校验，漂移时退出码 1
    python scripts/check_serve_sync.py --fix      # 把开发副本的加固同步到生产副本

退出码：
    0 = 一致（或 --fix 后已同步）
    1 = 存在漂移
    2 = 目录结构异常
"""
from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_SERVE = PROJECT_ROOT / "scripts" / "serve.py"
PROD_SERVE = (
    PROJECT_ROOT / "plugins" / "dsh-self-evolving-agent" / "scripts" / "serve.py"
)

# 需要保持「加固一致」的符号（函数或方法）
SECURITY_SYMBOLS = ("_iter_limited_lines", "stats", "planner_plan")


def _extract_def(text: str, name: str) -> str | None:
    """从源码中提取 `def <name>` 的整个定义体（含签名行，按缩进收尾）。

    同时匹配模块级函数（无前导缩进）与方法（有前导缩进）。
    返回空白归一后的文本；找不到返回 None。
    """
    lines = text.splitlines()
    pat = re.compile(rf"^(\s*)def {re.escape(name)}\b")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        indent = len(line) - len(line.lstrip())
        body = [line]
        for j in range(i + 1, len(lines)):
            cur = lines[j]
            if cur.strip() == "":
                body.append(cur)
                continue
            cur_indent = len(cur) - len(cur.lstrip())
            if cur_indent <= indent:
                break
            body.append(cur)
        # 空白归一：去除整体公共缩进、首尾空行、折叠行内多余空白
        dedented = textwrap.dedent("\n".join(body))
        normalized = "\n".join(
            ln.rstrip() for ln in dedented.strip().splitlines()
        )
        return normalized
    return None


def _load(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check() -> list[str]:
    """返回存在漂移的符号名列表（空列表 = 一致）。"""
    dev_text = _load(DEV_SERVE)
    prod_text = _load(PROD_SERVE)
    drifted: list[str] = []
    for name in SECURITY_SYMBOLS:
        dev_body = _extract_def(dev_text, name)
        prod_body = _extract_def(prod_text, name)
        if dev_body is None or prod_body is None:
            drifted.append(name)
            continue
        if dev_body != prod_body:
            drifted.append(name)
    return drifted


def sync() -> None:
    """把生产副本中 3 个安全方法的体，替换为开发副本的对应体。

    仅替换方法体，不触碰生产副本的引导/路径发现代码（_discover_project_root
    等），因此不会破坏双副本在引导层的有意差异。
    """
    dev_lines = _load(DEV_SERVE).splitlines()
    prod_lines = _load(PROD_SERVE).splitlines()

    def _body_lines(src_lines: list[str], name: str) -> tuple[int, int, list[str]]:
        pat = re.compile(rf"^(\s*)def {re.escape(name)}\b")
        for i, line in enumerate(src_lines):
            m = pat.match(line)
            if not m:
                continue
            indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < len(src_lines):
                cur = src_lines[j]
                if cur.strip() == "":
                    j += 1
                    continue
                if (len(cur) - len(cur.lstrip())) <= indent:
                    break
                j += 1
            return i, j, src_lines[i:j]
        raise RuntimeError(f"未找到 {name}")

    # 收集开发副本的体
    dev_bodies = {n: _body_lines(dev_lines, n) for n in SECURITY_SYMBOLS}

    # 在生产副本中定位并替换
    out: list[str] = []
    i = 0
    while i < len(prod_lines):
        matched = None
        for name in SECURITY_SYMBOLS:
            if re.match(rf"^(\s*)def {re.escape(name)}\b", prod_lines[i]):
                matched = name
                break
        if matched is None:
            out.append(prod_lines[i])
            i += 1
            continue
        # 计算生产副本中该方法的区间
        di, dj, _ = _body_lines(prod_lines, matched)
        # 用开发副本的体替换（保持生产副本的原始缩进前缀）
        _, _, dev_block = dev_bodies[matched]
        # 计算生产副本方法的缩进，整块平移
        prod_indent = len(prod_lines[di]) - len(prod_lines[di].lstrip())
        dev_indent = len(dev_block[0]) - len(dev_block[0].lstrip())
        delta = prod_indent - dev_indent
        for blk in dev_block:
            if blk.strip() == "":
                out.append("")
                continue
            cur_indent = len(blk) - len(blk.lstrip())
            out.append(" " * (cur_indent + delta) + blk.lstrip())
        i = dj  # 跳过原生产副本该方法

    PROD_SERVE.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix", action="store_true", help="发现漂移时把开发副本加固同步到生产副本"
    )
    args = parser.parse_args()

    for label, p in (("开发副本", DEV_SERVE), ("生产副本", PROD_SERVE)):
        if not p.is_file():
            print(f"[ERROR] 缺少 {label}: {p}")
            return 2

    drifted = check()
    if not drifted:
        print(f"[OK] serve.py 双副本安全加固一致（校验 {len(SECURITY_SYMBOLS)} 个符号）")
        return 0

    print("[DRIFT] 生产副本缺失/不一致的安全加固：")
    for name in drifted:
        print(f"  ~ {name}")

    if args.fix:
        sync()
        print("[FIX] 已用开发副本的加固覆盖生产副本对应方法体")
        return 0

    print("\n修复方式：")
    print("  python scripts/check_serve_sync.py --fix")
    return 1


if __name__ == "__main__":
    sys.exit(main())
