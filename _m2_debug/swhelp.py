# -*- coding: utf-8 -*-
"""把 SolidWorks API 帮助 HTML 转成纯文本打印。

用法：
    python swhelp.py <匹配片段> [匹配片段2 ...]
    对每个片段，在 C:\\Temp\\swapihtml 里找文件名包含该片段的页面，打印正文。
"""
import os
import re
import html
import sys

BASE = r"C:\Temp\swapihtml"


def text(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)</(p|div|tr|li|h1|h2|h3|dt|dd|pre)>", "\n", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
    t = re.sub(r"(?s)<[^>]+>", "", raw)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return "\n".join(l.strip() for l in t.split("\n") if l.strip())


def find(pat):
    hits = []
    for dirpath, _dirs, files in os.walk(BASE):
        for f in files:
            if f.lower().endswith((".html", ".htm")) and pat.lower() in f.lower():
                hits.append(os.path.join(dirpath, f))
    return sorted(hits)


def main():
    pats = sys.argv[1:]
    if not pats:
        print(__doc__)
        return
    for pat in pats:
        hs = find(pat)
        print("#" * 90)
        print("# 匹配 '%s' -> %d 个页面" % (pat, len(hs)))
        print("#" * 90)
        for h in hs[:3]:
            print("-" * 90)
            print("## " + os.path.basename(h))
            print("-" * 90)
            print(text(h)[:20000])
        print()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
