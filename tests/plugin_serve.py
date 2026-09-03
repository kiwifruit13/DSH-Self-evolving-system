"""加载 Cordis 插件内的 serve.py（生产副本）供测试使用。

历史背景：项目根目录曾存在一份 `scripts/serve.py`（开发副本），与插件内的生产
副本构成双副本 —— 这正是 BUG-51（P0）的成因：安全加固只落到开发副本，生产路径
因此长期裸奔。2026-09-03 根副本被删除，插件版成为唯一真源，其
`_discover_project_root()` 在 `DSH_DEV=1` 时可回溯宿主 `src/`，已完整覆盖原
开发副本的用途。

测试因此统一指向生产副本，杜绝「测的是 A、跑的是 B」的错配重演。

用法：
    from tests.plugin_serve import srv      # 访问 srv.Server 等
    from tests.plugin_serve import Server   # 只要类时
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SERVE_PATH = (
    _PROJECT_ROOT / "plugins" / "dsh-self-evolving-agent" / "scripts" / "serve.py"
)

if not _SERVE_PATH.is_file():
    raise RuntimeError(f"插件版 serve.py 缺失：{_SERVE_PATH}")

_spec = importlib.util.spec_from_file_location("dsh_plugin_serve", _SERVE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"无法为 serve.py 构建模块规格：{_SERVE_PATH}")

srv = importlib.util.module_from_spec(_spec)
# 先登记再执行：serve.py 的模块级代码会 import src.*，提前登记可避免循环导入
sys.modules["dsh_plugin_serve"] = srv
_spec.loader.exec_module(srv)

Server = srv.Server

__all__ = ["Server", "srv"]
