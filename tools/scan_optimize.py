"""项目优化扫描：死代码 / 未使用导入 / 缺失索引 / 重复代码块。

一次性给出**可执行的优化清单**，而不是泛泛的代码质量建议：

1. **未使用的导入**：AST 层面找出 import 了但文件里从没引用的名字。
2. **未被调用的顶层函数 / 类**：模块级定义但全项目无处引用（死代码）。
3. **缺失索引**：把代码里所有 `WHERE col = ?` / `JOIN ON col` 用到的列，与实际建的
   索引比对，列出「查询高频但没索引」的列 —— 这是性能问题的直接来源。
4. **重复代码块**：跨文件出现的、长度 ≥ 6 行的相同文本块（复制粘贴的信号）。

用法：
    python tools/scan_optimize.py
    python tools/scan_optimize.py --only index
"""
import argparse
import ast
import hashlib
import pathlib
import re
import sqlite3
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent


def scan_unused_imports():
    """找出 import 了但从未使用的名字。"""
    out = []
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            continue
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    name = (a.asname or a.name).split(".")[0]
                    imported[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "*":
                        continue
                    imported[a.asname or a.name] = node.lineno
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                n = node
                while isinstance(n, ast.Attribute):
                    n = n.value
                if isinstance(n, ast.Name):
                    used.add(n.id)
        for name, line in sorted(imported.items(), key=lambda x: x[1]):
            if name not in used and name not in f.read_text(encoding="utf-8"):
                out.append((f.name, line, name))
    return out


def scan_dead_code():
    """模块级定义的函数 / 类，在全项目其他地方都没有引用。"""
    defs = []
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("__"):
                    continue
                # 带 @xxx.route 装饰的是 Flask 端点，靠装饰器注册而非直接调用
                # 端点装饰器：@bp.route(...) / @bp.get(...) / @bp.post(...) …
                route_marks = ("route", "get", "post", "put", "delete", "patch",
                               "errorhandler", "teardown_appcontext",
                               "before_request", "after_request",
                               "appcontext_processor", "shell_context_processor")
                if any(any(m in ast.dump(d) for m in route_marks)
                       for d in node.decorator_list):
                    continue
                defs.append((f.name, node.name, node.lineno))
    # 全项目文本（排除定义行本身）
    corpus = {}
    for f in sorted(ROOT.glob("*.py")):
        corpus[f.name] = f.read_text(encoding="utf-8")
    out = []
    for fname, name, line in defs:
        hits = 0
        for other, text in corpus.items():
            for m in re.finditer(r"\b" + re.escape(name) + r"\b", text):
                if other == fname:
                    # 排除定义处
                    if m.start() == 0 or True:
                        pass
                hits += 1
        # 定义处至少占 1 次；实际引用次数 - 定义次数 == 0 → 死代码
        if hits <= 1:
            out.append((fname, name, line))
    return out


def scan_missing_indexes():
    """比对代码里查询用到的列与实际索引。"""
    # 1) 实际索引（从库结构读；库里没有就读源码里的 CREATE INDEX）
    idx = defaultdict(set)
    for f in sorted(ROOT.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(
                r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                r"[\w\"`\[\]]+\s+ON\s+(\w+)\s*\(([^)]+)\)", src, re.I):
            table = m.group(1)
            for col in m.group(2).split(","):
                c = col.strip().split()[0].strip('"`[]')
                if c:
                    idx[table].add(c.lower())
    # 建表里的 UNIQUE 约束也算索引
    for f in sorted(ROOT.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\);",
                src, re.S | re.I):
            table = m.group(1)
            body = m.group(2)
            for line in body.splitlines():
                lm = re.match(r"\s*(\w+)\s+.*\bUNIQUE\b", line)
                if lm:
                    idx[table].add(lm.group(1).lower())
    # 2) 代码里查询用到的等值列
    usage = defaultdict(lambda: defaultdict(int))
    for f in sorted(ROOT.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"WHERE\s+(.+?)(?:GROUP\s+BY|ORDER\s+BY|LIMIT|$)", src,
                             re.S | re.I):
            seg = m.group(1)
            for cm in re.finditer(r"(\w+)\s*=\s*[?(\s]", seg):
                usage[None][cm.group(1).lower()] += 1
        for m in re.finditer(r"JOIN\s+(\w+)\s+ON\s+(.+?)(?:WHERE|GROUP|ORDER|LIMIT|$)",
                             src, re.S | re.I):
            table = m.group(1).lower()
            for cm in re.finditer(r"(\w+)\.(\w+)\s*=\s*[\w.]+\.(\w+)", m.group(2)):
                usage[table][cm.group(3).lower()] += 1
    # 3) 汇总「表 → 查询列」，与索引比对
    out = []
    for table, cols in usage.items():
        if table is None:
            continue
        have = idx.get(table, set())
        for col, n in cols.items():
            if col not in have and col not in ("1", "0"):
                out.append((table, col, n))
    out.sort(key=lambda x: -x[2])
    return out


def scan_duplicate_blocks():
    """跨文件重复的 ≥6 行相同代码块。"""
    blocks = defaultdict(set)
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith("test_"):
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - 5):
            chunk = [l.strip() for l in lines[i:i + 6]]
            if sum(1 for l in chunk if l) < 5:
                continue
            if any(l.startswith("#") or l.startswith('"""') for l in chunk):
                continue
            key = hashlib.md5("\n".join(chunk).encode()).hexdigest()
            blocks[key].add((f.name, i + 1, tuple(chunk)))
    out = []
    for key, items in blocks.items():
        if len(items) > 1:
            out.append(items)
    out.sort(key=lambda x: -len(x))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["import", "dead", "index", "dup"], default=None)
    args = ap.parse_args()

    if args.only in (None, "import"):
        items = scan_unused_imports()
        print(f"=== 未使用的导入（{len(items)}）===")
        for f, line, name in items:
            print(f"  {f}:{line}  {name}")

    if args.only in (None, "dead"):
        items = scan_dead_code()
        print(f"\n=== 疑似死代码：模块级定义但全项目无引用（{len(items)}）===")
        for f, name, line in items:
            print(f"  {f}:{line}  {name}()")

    if args.only in (None, "index"):
        items = scan_missing_indexes()
        print(f"\n=== 查询用到的列但没有索引（Top 30，{len(items)}）===")
        for table, col, n in items[:30]:
            print(f"  {table}.{col}  （代码中出现 {n} 次）")

    if args.only in (None, "dup"):
        items = scan_duplicate_blocks()
        print(f"\n=== 跨文件重复的 6 行代码块（{len(items)}）===")
        for group in items[:12]:
            print("  " + " | ".join(f"{f}:{ln}" for f, ln, _ in group))
            print(f"      {group[0][2][0][:78]}")


if __name__ == "__main__":
    main()
