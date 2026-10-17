# -*- coding: utf-8 -*-
"""B9: mold_fix.py silent-except logging patch (line-based, no regex backslashes)."""
import io
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'mold_fix.py'
with io.open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

logger_block = '''

# ---------------------------------------------------------------------------
# 调试日志（B9 修复：静默 except 日志化，2026-10-17）
# ---------------------------------------------------------------------------
# 之前大量 `except Exception: pass/continue` 会把几何流程中的真实故障
# （布尔失败、拓扑异常、导入错误）吞掉，只表现为"结果不对"而无任何线索。
# 这里统一接一个 logger，静默分支改为 debug 级记录，行为不变但可诊断。
import logging as _logging

_log = _logging.getLogger('mold_fix')
if not _log.handlers:
    _h = _logging.StreamHandler()
    _h.setFormatter(_logging.Formatter('[mold_fix] %(levelname)s %(message)s'))
    _log.addHandler(_h)
    _log.setLevel(_logging.INFO)
'''

# bodies that are truly "swallow" (silent drop). return-fallbacks stay as-is.
SWALLOW = ('pass', 'continue')

out = []
i = 0
n = len(lines)
logger_done = False
n_patched = 0
n_skipped = 0

while i < n:
    line = lines[i]
    # insert logger right after the OCP.BRepCheck import line
    if not logger_done and line.rstrip() == 'from OCP.BRepCheck import BRepCheck_Analyzer':
        out.append(line)
        out.append(logger_block)
        logger_done = True
        i += 1
        continue

    stripped = line.strip()
    if stripped == 'except Exception:' and 'as _e' not in stripped:
        indent = line[:len(line) - len(line.lstrip())]
        # look ahead to first non-blank line
        j = i + 1
        blanks = []
        while j < n and not lines[j].strip():
            blanks.append(lines[j])
            j += 1
        body = lines[j].strip() if j < n else ''
        if body in SWALLOW:
            # rewrite except to capture + insert debug log before the swallow
            out.append(indent + 'except Exception as _e:\n')
            out.append(indent + '    _log.debug("swallowed exception (was silent) near line %d: %%s", _e)\n' % (i + 1))
            for b in blanks:
                out.append(b)
            out.append(lines[j])
            n_patched += 1
            i = j + 1
            continue
        else:
            n_skipped += 1
    out.append(line)
    i += 1

with io.open(path, 'w', encoding='utf-8') as f:
    f.writelines(out)

print('logger inserted:', logger_done)
print('silent except patched:', n_patched)
print('except with real body left as-is:', n_skipped)
