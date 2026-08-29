"""第七批安全加固回归测试：F-8 维护日志滚动 / R4 请求体限长 / R2 路径发现防错配。

这些用例专门覆盖"工程健壮性"类缺陷，避免它们再次退化为静默风险。
运行：
    python -m pytest tests/test_serve_security.py -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

# ── 被测模块定位 ──────────────────────────────────────────────────────────
# 插件版 serve.py 是一个"自发现根目录"的脚本：导入即触发 _discover_project_root()，
# 因此必须能被 import。这里用 importlib 从绝对路径加载，不污染 sys.modules 命名。
_PLUGIN_SERVE = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "dsh-self-evolving-agent"
    / "scripts"
    / "serve.py"
)


def _load_serve_module():
    spec = importlib.util.spec_from_file_location("dsh_plugin_serve_under_test", _PLUGIN_SERVE)
    module = importlib.util.module_from_spec(spec)
    # 确保项目根（含 pycore）在 sys.path，使 serve.py 的 import src.* 成功
    project_root = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "dsh-self-evolving-agent"
    )
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    spec.loader.exec_module(module)
    return module


# ── F-8：maintenance_log 滚动上限 ────────────────────────────────────────
def test_maintenance_log_rolling_keeps_latest():
    """超过 MAX_MAINTENANCE_LOG 条时丢弃最旧、保留最新，且总数有界。"""
    from src.models import LocalMindMap, MAX_MAINTENANCE_LOG

    lm = LocalMindMap(node_id="n", parent_path="", focus_description="",
                      boundary_rules="", logic_signature="")
    for i in range(MAX_MAINTENANCE_LOG + 30):
        lm.append_log("event", f"entry-{i}", "sub_agent")

    assert len(lm.maintenance_log) == MAX_MAINTENANCE_LOG
    # 最旧 30 条被丢弃，最新应为最后写入的那条
    assert lm.maintenance_log[-1].reason == "entry-{}".format(MAX_MAINTENANCE_LOG + 29)
    assert lm.maintenance_log[0].reason == "entry-{}".format(30)


def test_maintenance_log_within_limit_unchanged():
    """不超过上限时日志完整保留，不丢任何条目。"""
    from src.models import LocalMindMap, MAX_MAINTENANCE_LOG

    lm = LocalMindMap(node_id="n", parent_path="", focus_description="",
                      boundary_rules="", logic_signature="")
    for i in range(MAX_MAINTENANCE_LOG - 5):
        lm.append_log("event", f"e-{i}", "sub_agent")
    assert len(lm.maintenance_log) == MAX_MAINTENANCE_LOG - 5


# ── R4：请求体单行字节上限 ───────────────────────────────────────────────
def test_r4_oversized_line_yields_none_sentinel():
    """单行超过 MAX_LINE_BYTES 时产出 None 哨兵（不把整行读入解析）。"""
    mod = _load_serve_module()
    over = b"x" * (mod.MAX_LINE_BYTES + 100)  # 无换行符
    out = list(mod._iter_limited_lines(io.BytesIO(over)))
    assert out == [None]


def test_r4_normal_line_passthrough():
    """正常长度的行原样产出。"""
    mod = _load_serve_module()
    line = b'{"jsonrpc":"2.0","method":"health"}\n'
    out = list(mod._iter_limited_lines(io.BytesIO(line)))
    assert out == [line]


def test_r4_oversized_then_normal_continues():
    """超长行（无换行，真超限）被排空后，其后正常行仍可被正确处理。

    关键点：超长时不把整行读完再判断，而是分片读并排空剩余字节，
    否则后续正常请求会被饿死。这里用「无换行 + 真超限」的流来验证排空逻辑。
    """
    mod = _load_serve_module()
    # 一段真超限且无换行的字节，模拟失控客户端只发一个巨型请求
    over = b"y" * (mod.MAX_LINE_BYTES + 1024)
    normal = b'{"jsonrpc":"2.0","method":"health"}\n'
    # 超长块与正常行之间用换行分隔，确保正常行是独立的一行
    stream = io.BytesIO(over + b"\n" + normal)
    out = list(mod._iter_limited_lines(stream))
    assert None in out  # 超长块被标记为丢弃
    assert normal in out  # 其后正常行仍被正确产出
    assert out[-1] == normal


def test_r4_serve_returns_32600_on_oversized():
    """_serve 对超长哨兵返回 JSON-RPC -32600 错误，不参与解析。"""
    mod = _load_serve_module()
    server = mod.Server.__new__(mod.Server)  # 不需要完整初始化

    captured = []
    server._serve([None], lambda s: captured.append(s))
    assert len(captured) == 1
    payload = json.loads(captured[0])
    assert payload["error"]["code"] == -32600


def test_r4_serve_handles_valid_request():
    """_serve 对合法请求正常调度（回归：限长改造不能破坏正常路径）。"""
    mod = _load_serve_module()
    server = mod.Server.__new__(mod.Server)
    # 用桩替换真实 _handle，避免依赖数据库；只验证分发接线不被限长改造破坏
    server._handle = lambda method, params: {"jsonrpc": "2.0", "result": {"ok": True}}

    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}})
    captured = []
    server._serve([req], lambda s: captured.append(s))
    assert len(captured) == 1
    payload = json.loads(captured[0])
    assert payload.get("result", {}).get("ok") is True
    assert payload.get("id") == 1


# ── R2：路径发现防错配 ───────────────────────────────────────────────────
def test_r2_prefers_pycore_over_host_src(monkeypatch):
    """未设开发标志时，必须优先命中包内 pycore，而非误用宿主 src。"""
    monkeypatch.delenv("DSH_DEV", raising=False)
    monkeypatch.delenv("SELF_EVOLVING_DEV", raising=False)
    mod = _load_serve_module()
    root = mod._discover_project_root()
    # 本仓库根也存在 src/main_agent.py，旧实现会误命中它；
    # 新实现必须返回 pycore 路径，杜绝版本错配。
    assert root.name == "pycore"
    assert (root / "src" / "main_agent.py").is_file()


def test_r2_dev_flag_allows_host_src(monkeypatch):
    """开发标志开启时，优先回溯宿主 src/ 用于本地联调（live-edit 即时生效）。"""
    monkeypatch.setenv("DSH_DEV", "1")
    mod = _load_serve_module()
    root = mod._discover_project_root()
    # 开发态优先宿主 src：本仓库根确实有 src/main_agent.py
    assert root.name != "pycore"
    assert (root / "src" / "main_agent.py").is_file()


def test_r2_no_flag_never_uses_host_src(monkeypatch):
    """未设开发标志时，绝不加载宿主 src，只走 pycore（R2 防错配的核心）。"""
    monkeypatch.delenv("DSH_DEV", raising=False)
    monkeypatch.delenv("SELF_EVOLVING_DEV", raising=False)
    mod = _load_serve_module()
    root = mod._discover_project_root()
    assert root.name == "pycore"
