"""为 src 包生成 API 参考文档。

以代码为唯一真相源（import + inspect 反射），不手写文档：
改 API 描述 → 改代码 docstring；改签名 → 改代码，然后重跑本脚本。

用法：
    python scripts/gen_api_docs.py        # 覆写 api_reference.md

契约门禁（与代码同步提交）：
    python scripts/gen_api_docs.py && git diff --exit-code -- api_reference.md
"""
import importlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

MODULES = [
    "src.models",
    "src.storage",
    "src.tag_system",
    "src.tag_query",
    "src.scoring",
    "src.pending_queue",
    "src.overlap_checker",
    "src.offline_planner",
    "src.routing_table",
    "src.skill_compiler",
    "src.main_agent",
    "src.sub_agent",
    # 补全：此前遗漏了 sub_agent_pool，该模块的公开 API（含 SubAgentPool /
    # SpecializedSubAgent 的构造签名）从未进入文档，改了签名文档无从体现。
    "src.sub_agent_pool",
]


def _fmt_sig(sig: inspect.Signature) -> str:
    parts = []
    for p in sig.parameters.values():
        ann = ""
        if p.annotation != inspect.Parameter.empty:
            if hasattr(p.annotation, "__name__"):
                ann = f": {p.annotation.__name__}"
            else:
                ann = f": {p.annotation!r}"
        dflt = ""
        if p.default != inspect.Parameter.empty:
            dflt = f" = {p.default!r}"
        parts.append(f"{p.name}{ann}{dflt}")
    ret = ""
    if sig.return_annotation != inspect.Signature.empty:
        if hasattr(sig.return_annotation, "__name__"):
            ret = f" -> {sig.return_annotation.__name__}"
        else:
            ret = f" -> {sig.return_annotation!r}"
    return f"({', '.join(parts)}){ret}"


def _summary(doc: str | None) -> str:
    """取 docstring 的摘要行（首行非空文本），避免方法列表被长文档淹没。"""
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _annotation_name(ann: object) -> str:
    """渲染类型注解：优先 __name__，字符串注解（PEP 563）回退到字面量。"""
    if ann is inspect.Parameter.empty or ann is inspect.Signature.empty:
        return ""
    if isinstance(ann, str):
        return ann
    name = getattr(ann, "__name__", None)
    return name if name else repr(ann)


def _method_lines(cls: type) -> list[str]:
    """列出类**自身定义**的公开方法与属性（不含继承、不含 dunder）。

    只收录本类定义，避免把 object/Exception 的继承成员灌进文档；
    按名字排序，保证生成结果稳定（否则 git diff 门禁全是噪音）。
    """
    out: list[str] = []
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_"):
            continue

        if isinstance(member, property):
            # 属性：无调用签名，展示返回类型
            ret = ""
            fget = member.fget
            if fget is not None:
                ret = _annotation_name(
                    getattr(fget, "__annotations__", {}).get("return", "")
                )
            summary = _summary(inspect.getdoc(fget) or inspect.getdoc(member))
            suffix = f" → `{ret}`" if ret else ""
            extra = f" — {summary}" if summary else ""
            out.append(f"  - 属性: `{name}`{suffix}{extra}")
            continue

        target = member
        if isinstance(member, (staticmethod, classmethod)):
            target = member.__func__
        if not (inspect.isfunction(target) or inspect.ismethod(target)):
            continue

        summary = _summary(inspect.getdoc(target))
        try:
            sig = inspect.signature(target)
            rendered = _fmt_sig(sig)
        except (ValueError, TypeError):
            rendered = "(签名不可获取)"
        # 去掉签名的 self/cls 首参，阅读更贴近调用形式
        rendered = rendered.replace("(self, ", "(").replace("(self)", "()")
        rendered = rendered.replace("(cls, ", "(").replace("(cls)", "()")
        extra = f" — {summary}" if summary else ""
        out.append(f"  - 方法: `{name}{rendered}`{extra}")

    return out


def generate() -> str:
    lines = [
        "# src API 参考文档（自动生成）",
        "",
        "> 本文档由 `scripts/gen_api_docs.py` 自动生成，以代码为唯一真相源。",
        "> 若需更新 API 描述，请修改代码 docstring 后重新生成。",
        "",
        "---",
        "",
    ]

    for mod_name in MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue

        mod_doc = inspect.getdoc(mod)
        short_name = mod_name.replace("src.", "")
        lines.append(f"## `{short_name}`")
        lines.append("")
        if mod_doc:
            lines.append(f"*{mod_doc.strip()}*")
            lines.append("")

        for name in sorted(dir(mod)):
            if name.startswith("_"):
                continue
            obj = getattr(mod, name)
            if not inspect.isclass(obj) and not inspect.isfunction(obj):
                continue
            # 只收录在本模块定义的类/函数，跳过导入的
            if obj.__module__ != mod.__name__:
                continue

            if inspect.isclass(obj):
                doc = inspect.getdoc(obj)
                lines.append(f"### `{obj.__name__}`")
                lines.append("")
                if doc:
                    lines.append(f"*{doc.strip()}*")
                    lines.append("")
                try:
                    sig = inspect.signature(obj.__init__)
                    lines.append(f"- 签名: `{obj.__name__}{_fmt_sig(sig)}`")
                except (ValueError, TypeError):
                    pass
                lines.append("")
                # 公开方法/属性：方法的参数默认值与返回类型同样是 API 契约，
                # 此前只输出构造签名，导致方法级变更（增删参数、改默认值）
                # 在文档里完全不可见，无法充当契约门禁。
                methods = _method_lines(obj)
                if methods:
                    lines.append("公开成员：")
                    lines.append("")
                    lines.extend(methods)
                    lines.append("")
            elif inspect.isfunction(obj):
                doc = inspect.getdoc(obj)
                lines.append(f"### `{obj.__name__}`")
                lines.append("")
                if doc:
                    lines.append(f"*{doc.strip()}*")
                    lines.append("")
                try:
                    sig = inspect.signature(obj)
                    lines.append(f"- 签名: `{obj.__name__}{_fmt_sig(sig)}`")
                except (ValueError, TypeError):
                    pass
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    doc = generate()
    Path("api_reference.md").write_text(doc, encoding="utf-8")
    print(f"api_reference.md: {len(doc)} chars")
