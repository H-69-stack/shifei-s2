# -*- coding: utf-8 -*-
"""查 InsertCutSurface 在类型库里的真实参数类型（VARIANT 码）。"""
import re, glob, os

base = os.path.join(os.environ["TEMP"], "gen_py", "3.10")
txt = ""
for f in glob.glob(os.path.join(base, "*.py")):
    txt += open(f, encoding="utf-8", errors="replace").read()

print("=== FeatureManager 名字含 Surface / Cut 的方法 ===")
names = sorted(set(re.findall(r"def (\w*(?:CutSurface|SurfaceCut|CutWithSurface)\w*)\(", txt)))
print("  ", names if names else "(无)")

print("\n=== InsertCutSurface vtable 定义 ===")
for m in re.finditer(r"\(\s*'InsertCutSurface'\s*,\s*'Retval'\s*,\s*\),\s*(\d+),\s*\((.*?)\)\s*,", txt, re.S):
    print(m.group(0)[:1200])
    print("-" * 60)

print("\n=== 对照：FeatureExtrusion3 的 vtable 前 200 字符 ===")
m = re.search(r"\(\s*'FeatureExtrusion3'\s*,\s*'Retval'\s*,\s*\),\s*\d+,\s*\((.*?)\)\s*,", txt, re.S)
print(m.group(0)[:400] if m else "(未找到)")
