#!/usr/bin/env python3
"""校验插件包内 pycore/ 是否为项目根 src/ 的最新快照。

背景（第七批 F-1）：
    `plugins/dsh-self-evolving-agent/pycore/` 是 `src/` 的**构建产物**
    （被 .gitignore 忽略，由 `npm run prepack` → `prepare-pycore.mjs` 重新生成）。

    正常运行路径下它是自动保持一致的：
      - `npm pack` / `npm publish` → 先跑 prepack 再打包，且 package.json 的
        `files: ["pycore/"]` 白名单会覆盖 .gitignore ⇒ 产物一定是新的；
      - 开发态 `dsh plugin add <项目内插件路径>` → serve.py 向上回溯命中
        项目根 `src/` ⇒ 直接用真源，不经过 pycore。

    但存在一条**不受 prepack 保护**的路径：
      - 把插件目录**单独复制**出去（脱离项目根，祖先目录无 `src/main_agent.py`），
        再以本地路径安装 ⇒ 既没有 prepack，回溯也失败 ⇒ 加载的是**过期的 pycore**。

    本脚本用于把这类漂移在 CI / 发布前拦下来。

用法：
    python scripts/check_pycore_sync.py            # 仅校验，漂移时退出码 1
    python scripts/check_pycore_sync.py --fix      # 校验并自动同步

退出码：
    0 = 一致（或 --fix 后已同步）
    1 = 存在漂移
    2 = 目录结构异常（缺少 src/ 或 pycore/）
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
PYCORE_DIR = (
    PROJECT_ROOT / "plugins" / "dsh-self-evolving-agent" / "pycore" / "src"
)

# 构建产物/缓存，不参与一致性比较
IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo")


def _iter_rel_files(root: Path) -> set[str]:
    """列出 root 下用于比较的相对路径集合。"""
    if not root.is_dir():
        return set()
    out: set[str] = set()
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part == "__pycache__" for part in p.relative_to(root).parts):
            continue
        if p.suffix in (".pyc", ".pyo"):
            continue
        out.add(p.relative_to(root).as_posix())
    return out


def _differ(a: Path, b: Path) -> bool:
    try:
        return not filecmp.cmp(a, b, shallow=False)
    except OSError:
        return True


def check() -> tuple[list[str], list[str], list[str]]:
    """返回 (仅 src 有, 仅 pycore 有, 内容不同) 三组相对路径。"""
    src_files = _iter_rel_files(SRC_DIR)
    dst_files = _iter_rel_files(PYCORE_DIR)
    only_src = sorted(src_files - dst_files)
    only_dst = sorted(dst_files - src_files)
    differed = sorted(
        rel
        for rel in src_files & dst_files
        if _differ(SRC_DIR / rel, PYCORE_DIR / rel)
    )
    return only_src, only_dst, differed


def sync() -> None:
    """用 src/ 覆盖 pycore/src/（等价于 prepare-pycore.mjs 的 Python 版）。"""
    if PYCORE_DIR.exists():
        shutil.rmtree(PYCORE_DIR)
    PYCORE_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        SRC_DIR,
        PYCORE_DIR,
        ignore=shutil.ignore_patterns(*IGNORE_PATTERNS),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix", action="store_true", help="发现漂移时自动从 src/ 重新同步"
    )
    args = parser.parse_args()

    if not SRC_DIR.is_dir():
        print(f"[ERROR] 缺少目录: {SRC_DIR}")
        return 2

    if not PYCORE_DIR.is_dir():
        print(f"[WARN] pycore 尚未生成: {PYCORE_DIR}")
        if args.fix:
            sync()
            print(f"[FIX] 已从 {SRC_DIR} 生成 {PYCORE_DIR}")
            return 0
        print("       运行 `npm run prepack`（或加 --fix）生成")
        return 1

    only_src, only_dst, differed = check()
    drifted = bool(only_src or only_dst or differed)

    if not drifted:
        print(f"[OK] pycore 与 src 一致（{len(_iter_rel_files(SRC_DIR))} 个文件）")
        return 0

    print("[DRIFT] pycore 落后于 src：")
    for rel in only_src:
        print(f"  + 仅 src 有:     {rel}")
    for rel in only_dst:
        print(f"  - 仅 pycore 有:  {rel}")
    for rel in differed:
        print(f"  ~ 内容不同:      {rel}")

    if args.fix:
        sync()
        print(f"[FIX] 已同步 {SRC_DIR} -> {PYCORE_DIR}")
        return 0

    print("\n修复方式（任选其一）：")
    print("  1) cd plugins/dsh-self-evolving-agent && npm run prepack")
    print("  2) python scripts/check_pycore_sync.py --fix")
    return 1


if __name__ == "__main__":
    sys.exit(main())
