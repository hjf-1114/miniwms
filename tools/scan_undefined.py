"""静态扫描：找出「函数里用了某个全局名但模块级没定义」的真未定义变量。

这类缺陷在 Python 里表现为**运行时 NameError → 500**，单测与 E2E 很难覆盖到
（只有那条路径被真正调用时才会炸）。M32 巡检发现的「库位详情 500」就是
`routes_location.get_location()` 用了未定义的 `db` —— 自 M20-A 起潜伏，
直到有巡检工具去逐个点「编辑」才暴露。

实现用标准库 `symtable` 做作用域分析（能正确处理闭包与嵌套函数），
比朴素的 AST 名字收集准确得多。

用法：
    python tools/scan_undefined.py            # 扫描全部模块
    python tools/scan_undefined.py --verbose  # 附带说明
"""
import argparse
import builtins
import pathlib
import symtable
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILTINS = set(dir(builtins))


def scan_file(path):
    """返回该文件里「引用为全局但模块级未定义」的名字列表。"""
    src = path.read_text(encoding="utf-8")
    st = symtable.symtable(src, str(path), "exec")
    module_defined = {s.get_name() for s in st.get_symbols()
                      if s.is_assigned() or s.is_imported()}
    found = []

    def walk(table):
        for sym in table.get_symbols():
            name = sym.get_name()
            # 参数在 symtable 里同时标记 global 与 parameter，须排除；
            # 这里只关心「被当成全局引用」且模块级没有的名字
            if (sym.is_global() and not sym.is_assigned()
                    and not sym.is_parameter()):
                if name not in module_defined and name not in BUILTINS:
                    found.append((table.get_name(), name))
        for child in table.get_children():
            walk(child)

    for child in st.get_children():
        walk(child)
    # 去重（嵌套函数可能多处引用）
    seen = set()
    uniq = []
    for func, name in found:
        if (func, name) not in seen:
            seen.add((func, name))
            uniq.append((func, name))
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    total = 0
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith("test_") or f.name.startswith("e2e_"):
            continue
        for func, name in scan_file(f):
            total += 1
            print(f"{f.name:26} {func:30} 未定义: {name}")
    if total == 0:
        print("未发现未定义变量")
    else:
        print(f"\n共发现 {total} 处 —— 这些位置在运行到时会抛 NameError（HTTP 500）")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
