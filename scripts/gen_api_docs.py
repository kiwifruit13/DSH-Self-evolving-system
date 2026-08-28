"""为 src 包生成 API 参考文档。"""
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
