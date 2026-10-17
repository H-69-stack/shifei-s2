# -*- coding: utf-8 -*-
"""
mold_method3_solidworks.py —— ★模具生成法三（SolidWorks COM 二次开发）★
====================================================================================

【定位】
  用【SolidWorks 2024 的 API（COM 自动化）】实现"法二A 的几何"：

      产品实体.stp
        │  ① [Python/OCP] 投影法提取上/下形变面（沿用已验证的
        │     product_surface_classifier + mold_contact_surface_extractor）
        ├─→ xxx_PUNCH_CONTACT_FACES.stp  （产品上表面）
        └─→ xxx_CAVITY_CONTACT_FACES.stp （产品下表面）
              │  ② [SolidWorks COM] 用 SW 原生特征做出上/下模
              └─→ xxx_PUNCH_M3.stp / xxx_CAVITY_M3.stp

【为什么值得做】
  法二A 在 OCP 下的老毛病（上模顶面裁不平、下模顶面外甩 +6mm）根因是
  **开放曲面壳的布尔运算不可靠**。换成 SolidWorks 的「拉伸 + 成形到实体」
  与 `Body2.Operations2` 体级布尔，由 SW 内核完成，这类问题从根上消失。

【环境事实（本机实测，2026-09-11）】
  · SOLIDWORKS 2024 SP5（RevisionNumber 32.5.0），路径
    D:\\Program Files\\SW\\SOLIDWORKS\\SLDWORKS.exe
  · Python 3.10.8 / 64 位 + pywin32 312
  · `Dispatch('SldWorks.Application')` 0.0s 挂上已运行实例
  · `EnsureDispatch` 会失败（"can not automate the makepy process"）——必须
    事先 `python -m win32com.client.makepy <sldworks.tlb|swconst.tlb>`；
    本模块因此**不依赖 gen_py 缓存**，枚举值全部固化为常量。

【硬约束】
  1. SW COM 是 STA，**不能并发**：全模块共用一把 `threading.RLock`。
  2. 不是真无头：会拉起/复用 SLDWORKS.exe 并**占用一个许可证席位**。
     部署在无 SW 的机器上必须用 `available()` 探测后回退到 OCP 法二。
  3. 一次只处理一个文档：用完即 `CloseDoc`，不留脏状态。

【★API 实测要点（SOLIDWORKS 2024 SP5 / pywin32 312，逐条踩过）★】
  ① **中性格式必须用 `LoadFile4`，不能用 `OpenDoc6`**：
     `OpenDoc6` 对任何 STEP/IGES（连 OCP 导出的最简长方体）都返回
     `swFileRequiresRepairError(2097152)` 且不打开；`LoadFile4` 返回 `errors=0`。
     签名是 4 参：`LoadFile4(FileName, ArgString, ImportData, Errors)`，
     ImportData 由 `SldWorks.GetImportFileData(path)` 取得。
  ② **3D Interconnect 必须关闭**（否则 `LoadFile4` 返回 `swGenericError(1)`，
     且它会弹「要不要建派生零件」的模态框，静默模式无法应答 → 必然失败）。
     开关：`swMultiCAD_Enable3DInterconnect`(偏好号 691)，**必须重启 SW 才生效，
     且经 API 设置不持久化**。可靠的改法（SW 关闭时改注册表）：
       HKCU\\Software\\SolidWorks\\SOLIDWORKS 2024\\ImportSettings
         → MULTI_CAD_ENABLE_3D_INTERCONNECT = 0
  ③ **形变面 STEP 必须先缝合成单一壳再导出**：`mold_contact_surface_extractor`
     导出的是"面 Compound"，SW 会把每片面**拆成独立文档**
     （`Open CASCADE STEP translator 7.8 15.1 … 15.N`），拿去用只有一片面。
     用 `BRepBuilderAPI_Sewing` 缝成 1 个 SHELL 后，SW 才导入为 1 个曲面体。
  ④ **本机 gb 模板的基准面法向与标准命名相反**：`前视基准面` 才是 **Z 法向**
     （矩形→X/Y、拉伸→Z）；`上视基准面` 的法向是 Y。拉伸盒体请用 `前视基准面`。
  ⑤ **对象型参数不能传 Python `None`**，要传 `VARIANT(VT_DISPATCH, None)`：
     `SelectByID2` 的 Callout、`SaveAs` 的 ExportData 都会报"类型不匹配"。
     `Errors/Warnings` 这类 `[out] long*` 要传 `VARIANT(VT_BYREF|VT_I4)`。
  ⑥ **`SaveAs` 用 6 参形式**才成功：
     `SaveAs(Name, Version, Options, ExportData, Errors, Warnings)`。
  ⑦ **`Dispatch` 之后别立刻操作**：SW 翻译器还没加载完，调用会让 SW 进程
     直接崩（`远程过程调用失败` RPC 0x800706BE）。要等它真正就绪。
  ⑧ `ModelDoc2.GetBox()` 在 late-binding 下取不到，用 `Body2.GetBodyBox()`
     （返回**米**，顺序 `[xmin,ymin,zmin,xmax,ymax,zmax]`）。
  ⑨ `FeatureManager.InsertCutSurface`（用曲面切除）在 pywin32 late-binding 下
     **无法调用**（2 参/6 参都报 `The Python instance can not be converted to a
     COM object`）→ 改用「拉伸 + 终止条件 = 成形到实体」或 `Body2.Operations2`
     体级布尔绕开。
  ⑩ `EnsureDispatch` 会失败（"can not automate the makepy process"），需要先
     `python -m win32com.client.makepy <sldworks.tlb|swconst.tlb>`；本模块
     **不依赖 gen_py 缓存**，枚举值全部固化为常量。

【长度单位】SolidWorks API 内部一律用**米**，本项目其余部分用毫米 —— 进出必须换算。

【CLI】
  python mold_method3_solidworks.py --selftest            # 只验证 COM 管线
  python mold_method3_solidworks.py --probe-api           # 探测可用 API 面
  python mold_method3_solidworks.py --product 产品.stp --output-dir mold_method3
  python mold_method3_solidworks.py --upper 上形变面.stp --lower 下形变面.stp \
      --output-dir mold_method3

退出码：0=成功；2=参数/文件错误；1=运行异常。
"""

import os
import sys
import json
import time
import argparse
import threading

# 复用 mold_fix 的安全 print（Windows GBK 控制台下打印永不抛异常）
try:
    from mold_fix import install_safe_print
    install_safe_print(globals())
except Exception:      # pragma: no cover - 独立使用时不强依赖 OCP
    pass


# ---------------------------------------------------------------------------
# SolidWorks 枚举常量（实测自本机 2024 SP5 类型库，避免依赖 gen_py 缓存）
# ---------------------------------------------------------------------------
SW_DOC_PART = 1                       # swDocType_e.swDocPART
SW_ALL_BODIES = -1                    # swBodyType_e.swAllBodies
SW_SOLID_BODY = 0                     # swBodyType_e.swSolidBody
SW_END_COND_BLIND = 0                 # swEndConditions_e.swEndCondBlind
SW_END_COND_THROUGH_ALL = 1           # swEndConditions_e.swEndCondThroughAll
SW_END_COND_UP_TO_SURFACE = 4         # swEndConditions_e.swEndCondUpToSurface
SW_END_COND_UP_TO_BODY = 7            # swEndConditions_e.swEndCondUpToBody
SW_SAVE_AS_CURRENT_VERSION = 0        # swSaveAsVersion_e.swSaveAsCurrentVersion
SW_BODY_OP_INTERSECT = 15901          # swBodyOperationType_e.SWBODYINTERSECT
SW_BODY_OP_CUT = 15902                # swBodyOperationType_e.SWBODYCUT
SW_BODY_OP_ADD = 15903                # swBodyOperationType_e.SWBODYADD（并集）
SW_SEL_SOLIDBODIES = 76               # swSelectType_e.swSelSOLIDBODIES
SW_SEL_REFSURFACES = 27               # swSelectType_e.swSelREFSURFACES
SW_BODY_OP_NO_ERROR = 0               # swBodyOperationError_e.swBodyOperationNoError
SW_PREF_3D_INTERCONNECT = 691         # swUserPreferenceToggle_e.swMultiCAD_Enable3DInterconnect

DEFAULT_MARGIN = 5.0

# 全局 COM 会话（STA：必须串行）
_LOCK = threading.RLock()
_SW = None
_SW_VISIBLE = False


def _force_utf8():
    """Windows 控制台与 stdout 强制 UTF-8（中文不乱码）。"""
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass


def connect(visible=None):
    """取 SolidWorks COM 会话（单例，串行）。失败抛异常。"""
    global _SW, _SW_VISIBLE
    with _LOCK:
        if _SW is None:
            import win32com.client as win32
            _SW = win32.Dispatch("SldWorks.Application")
        if visible is not None and bool(visible) != _SW_VISIBLE:
            try:
                _SW.Visible = bool(visible)
                _SW_VISIBLE = bool(visible)
            except Exception:
                pass
        return _SW


def available():
    """探测 SolidWorks 是否可用（Web 端据此决定是否回退到 OCP 法二）。"""
    try:
        sw = connect()
        # 注意：RevisionNumber 是【属性】不是方法
        return bool(sw.RevisionNumber)
    except Exception:
        return False


def revision():
    """返回 SolidWorks 版本字符串（不可用时返回 None）。"""
    try:
        return connect().RevisionNumber
    except Exception:
        return None


def _quiet(sw):
    """关掉会让自动化卡住的交互项（失败一律忽略）。"""
    for pref in ("swInputDimValOnCreate", "swShowErrors", "swUseSystemSeparators"):
        try:
            sw.SetUserPreferenceToggle(getattr(sw, pref), False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------
class MoldMethod3Generator:
    """法三：SolidWorks COM 版的上/下模生成器。

    接口刻意与 `MoldMethod2Generator` 对齐（result dict 字段一致），
    这样导出 / 报告 / Web 下载链路可以直接复用。
    """

    def __init__(self, margin=DEFAULT_MARGIN, visible=False):
        self.margin = float(margin)
        self.visible = bool(visible)

    # ---------------- COM 基础 ----------------
    def _sw(self):
        sw = connect(self.visible)
        _quiet(sw)
        return sw

    def _open(self, filepath):
        """导入 STEP / SLDPRT，返回 ModelDoc2（失败抛异常）。

        ★实测要点（2026-09-11，SOLIDWORKS 2024 SP5）★
          · **必须用 `LoadFile4`，不能用 `OpenDoc6`**：本机实测 `OpenDoc6`
            对任何中性格式（STEP/IGES，连 OCP 导出的最简长方体也一样）都返回
            `swFileRequiresRepairError(2097152)` 且不打开；`LoadFile4` 返回
            `errors=0` 且实体正常进入。
          · `LoadFile4` 真实签名是 **4 个参数**：
            `LoadFile4(FileName, ArgString, ImportData, Errors)`；
            ImportData 用 `SldWorks.GetImportFileData(path)` 拿。
          · **前置条件**：3D Interconnect 必须关闭（否则 `LoadFile4` 返回
            `swGenericError(1)`），且该开关要重启 SW 才生效。
          · 刚 `Dispatch` 完别立刻导入 —— SW 翻译器还没加载完，调用会让
            SW 进程直接崩（`远程过程调用失败` RPC 0x800706BE）。
        """
        sw = self._sw()
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")
        path = os.path.abspath(filepath)
        _check_3dinterconnect(sw)

        try:
            import pythoncom
            from win32com.client import VARIANT
        except Exception:
            pythoncom = None
            VARIANT = None

        model = None
        err_code = None

        # 1) LoadFile4(FileName, ArgString, ImportData, Errors) —— 主路径
        if VARIANT is not None:
            try:
                import_data = sw.GetImportFileData(path)
                verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                model = sw.LoadFile4(path, "", import_data, verr)
                err_code = verr.value
            except Exception as e:
                err_code = f"LoadFile4 异常 {e!r}"
                model = None
        if model is not None:
            return model

        # 2) OpenDoc6 兜底（原生 .SLDPRT 走这条是通的）
        if VARIANT is not None:
            verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            vwarn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            try:
                model = sw.OpenDoc6(path, SW_DOC_PART, 1, "", verr, vwarn)
                err_code = verr.value
            except Exception as e:
                raise RuntimeError(f"SolidWorks 导入失败: {path} -> {e!r}")
        if model is not None:
            return model

        hint = ""
        if err_code == 2097152:
            hint = ("（swFileRequiresRepairError：中性格式请走 LoadFile4，"
                    "并确认 3D Interconnect 已关闭且已重启 SolidWorks）")
        elif err_code == 1:
            hint = "（swGenericError：通常是 3D Interconnect 还开着）"
        raise RuntimeError(
            f"SolidWorks 导入返回空文档: {path}  Errors={err_code} {hint}")

    def _close(self, model):
        if model is None:
            return
        try:
            self._sw().CloseDoc(model.GetTitle)
        except Exception:
            try:
                model.Close()
            except Exception:
                pass

    @staticmethod
    def _bodies(model):
        """取文档内所有体（Body2 列表）。"""
        try:
            bs = model.GetBodies2(SW_ALL_BODIES, True)
        except Exception as e:
            raise RuntimeError(f"GetBodies2 失败: {e!r}")
        if bs is None:
            return []
        return list(bs) if isinstance(bs, (tuple, list)) else [bs]

    @staticmethod
    def _solid_bodies(model):
        try:
            bs = model.GetBodies2(SW_SOLID_BODY, True)
        except Exception:
            return []
        if bs is None:
            return []
        return list(bs) if isinstance(bs, (tuple, list)) else [bs]

    def _body_op(self, target, tool, op, label="body-op"):
        """体级布尔（SW 的 Operation2）。返回结果 Body2。"""
        err = None
        try:
            res = target.Operations2(op, tool, err)
        except Exception as e:
            raise RuntimeError(f"{label} 布尔调用失败: {e!r}")
        if res is None:
            raise RuntimeError(f"{label} 布尔返回空（目标体可能被消耗）")
        return res

    def _save_step(self, model, path):
        """导出 STEP。

        ★实测★ `SaveAs(Name, Version, Options, ExportData, AdvancedSaveAsOptions,
        Errors, Warnings)` 里的 **ExportData / AdvancedSaveAsOptions 是对象参数，
        传 Python `None` 会报"类型不匹配"**，必须传 `VARIANT(VT_DISPATCH, None)`；
        Errors/Warnings 是 `[out] long*`，要传 `VARIANT(VT_BYREF|VT_I4)`。
        不同 SW 版本参数个数略有差异，这里逐个降级尝试。
        """
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        ext = model.Extension
        try:
            import pythoncom
            from win32com.client import VARIANT
            null = VARIANT(pythoncom.VT_DISPATCH, None)
            verr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            vwarn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        except Exception:
            null = verr = vwarn = None

        attempts = (
            (path, SW_SAVE_AS_CURRENT_VERSION, 1, null, null, verr, vwarn),
            (path, SW_SAVE_AS_CURRENT_VERSION, 1, null, verr, vwarn),
            (path, SW_SAVE_AS_CURRENT_VERSION, 1, null, null),
            (path, SW_SAVE_AS_CURRENT_VERSION, 1, null),
        )
        last = None
        for args in attempts:
            try:
                ext.SaveAs(*args)
                if os.path.exists(path):
                    return path
            except Exception as e:
                last = e
        raise RuntimeError(f"导出 STEP 失败: {path} ({last!r})")

    # ---------------- 结果 ----------------
    def _result(self, kind, input_file, model, details, volume=None):
        bb = self._model_bbox(model)
        return {
            'kind': kind,
            'input': input_file,
            'success': True,
            'details': details,
            'bbox': [round(v, 4) for v in bb],
            'volume': volume,
            '_model': model,
        }

    @staticmethod
    def _model_bbox(model):
        """文档所有实体的整体包围盒 (xmin,ymin,zmin,xmax,ymax,zmax)。"""
        return list(_bbox_of(model))

    def export_step(self, result, path):
        if not result.get('success'):
            print(f"  跳过导出（失败）: {path}")
            return None
        self._save_step(result['_model'], path)
        print(f"  导出 {result['kind']} STEP: {path}")
        return path

    def write_json(self, result, path):
        payload = {k: v for k, v in result.items() if not k.startswith('_')}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  导出 JSON 报告: {path}")
        return path


def _bbox_of(model):
    """整体包围盒 (xmin,ymin,zmin,xmax,ymax,zmax)。

    ★实测★ `ModelDoc2.GetBox()` 在 late-binding（CDispatch）下取不到
    （`AttributeError: <unknown>.GetBox`），改用各 `Body2.GetBodyBox()` 合并。
    """
    boxes = []
    try:
        bs = model.GetBodies2(SW_ALL_BODIES, True)
    except Exception:
        bs = None
    if bs is not None and not isinstance(bs, (tuple, list)):
        bs = [bs]
    for b in (bs or []):
        try:
            v = list(b.GetBodyBox())
            if len(v) >= 6:
                boxes.append([float(x) for x in v[:6]])
        except Exception:
            continue
    if not boxes:
        return (0.0,) * 6
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
            max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes))


_3DI_WARNED = [False]


def _check_3dinterconnect(sw):
    """3D Interconnect 开着时，中性格式经 API 导入必失败 —— 明确告警。

    实测（2026-09-11）：开着 → `LoadFile4` 返回 swGenericError(1)、
    `OpenDoc6` 返回 swFileRequiresRepairError(2097152)；
    关掉并重启 SW 后 → `LoadFile4` 返回 errors=0、实体正常进入。
    该开关**必须重启 SolidWorks 才生效**，且经 API 设置不持久化。
    """
    if _3DI_WARNED[0]:
        return
    try:
        if sw.GetUserPreferenceToggle(SW_PREF_3D_INTERCONNECT):
            _3DI_WARNED[0] = True
            print(
                "[法三] ⚠ 检测到 3D Interconnect 处于【开启】状态 —— STEP/IGES 经 API 导入会失败\n"
                "        （swFileRequiresRepairError 2097152 / swGenericError 1）。\n"
                "        请关闭后【重启 SolidWorks】：\n"
                "          工具 → 选项 → 系统选项 → 导入 → 取消勾选「启用 3D Interconnect」\n"
                "        或改注册表后重启：\n"
                r"          HKCU\Software\SolidWorks\SOLIDWORKS 2024\ImportSettings"
                r"  →  MULTI_CAD_ENABLE_3D_INTERCONNECT = 0")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ① 产品实体 → 上/下形变面（这步留在 OCP，已验证可靠）
# ---------------------------------------------------------------------------
def extract_contact_faces(product_filepath, output_dir, export_step=True):
    """调用既有提取器，从【产品】提取上/下形变面并导出 STEP。

    返回 {'punch': (result, step_path), 'cavity': (result, step_path)}
    """
    from mold_contact_surface_extractor import MoldContactSurfaceExtractor
    ext = MoldContactSurfaceExtractor()
    os.makedirs(output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(product_filepath))[0]
    out = {}
    for kind, fn, sfx in (('punch', ext.extract_punch, 'PUNCH'),
                          ('cavity', ext.extract_cavity, 'CAVITY')):
        res = fn(product_filepath)
        step_path = None
        if export_step:
            step_path = os.path.join(
                output_dir, f"{stem}_{sfx}_CONTACT_FACES.stp")
            ext.export_step(res, step_path)
        ext.write_json(res, os.path.join(
            output_dir, f"{stem}_{sfx}_CONTACT_FACES.json"))
        out[kind] = (res, step_path)
    return out


# ---------------------------------------------------------------------------
# 探测 / 自检
# ---------------------------------------------------------------------------
def probe_api():
    """探测方法3 需要用到的 SolidWorks API 面，打印可用性。"""
    _force_utf8()
    print("=" * 72)
    print("SolidWorks API 探测")
    print("=" * 72)
    try:
        sw = connect()
    except Exception as e:
        print("  [X] 无法连接 SolidWorks:", repr(e))
        return 1
    print(f"  RevisionNumber = {getattr(sw, 'RevisionNumber', '?')}")
    print(f"  Visible        = {getattr(sw, 'Visible', '?')}")
    print("\n  -- SldWorks 级 --")
    for n in ("OpenDoc6", "LoadFile4", "NewDocument", "CloseDoc", "GetDocuments",
              "Modeler", "GetModeler", "GetUserPreferenceStringValue",
              "SetUserPreferenceToggle", "ActiveDoc", "IActiveDoc2"):
        try:
            v = getattr(sw, n)
            print(f"    {n:30s} OK  callable={callable(v)}")
        except Exception as e:
            print(f"    {n:30s} --  {type(e).__name__}")
    print("\n  -- 新建零件并探测文档级 API --")
    model = None
    try:
        tpl = sw.GetUserPreferenceStringValue(8)   # swDefaultTemplatePart 常见值
        print(f"    默认零件模板 = {tpl!r}")
        if tpl and os.path.exists(tpl):
            model = sw.NewDocument(tpl, 0, 0, 0)
            print(f"    NewDocument -> {type(model).__name__}")
    except Exception as e:
        print("    NewDocument 失败:", repr(e))
    if model is not None:
        for n in ("GetBodies2", "SketchManager", "FeatureManager", "Extension",
                  "SelectByID2", "ClearSelection2", "GetBox", "EditRebuild3",
                  "InsertSketch", "ShowNamedView2"):
            try:
                v = getattr(model, n)
                print(f"    model.{n:26s} OK  callable={callable(v)}")
            except Exception as e:
                print(f"    model.{n:26s} --  {type(e).__name__}")
        try:
            sw.CloseDoc(model.GetTitle)
        except Exception:
            pass
    return 0


def selftest(step_file=None):
    """只验证 COM 管线：连接 → 导入 STEP → 取体 → 导出 → 关闭。"""
    _force_utf8()
    print("=" * 72)
    print("法三 COM 管线自检")
    print("=" * 72)
    t0 = time.time()
    if not available():
        print("  [X] SolidWorks 不可用")
        return 1
    print(f"  [1] 连接 OK  版本={revision()}  ({time.time()-t0:.1f}s)")

    if step_file is None:
        root = os.path.dirname(os.path.abspath(__file__))
        step_file = os.path.join(root, 'web_box_generator', 'uploads',
                                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')
    if not os.path.exists(step_file):
        print(f"  [!] 测试文件不存在，跳过导入测试: {step_file}")
        return 0

    g = MoldMethod3Generator(visible=False)
    model = None
    try:
        model = g._open(step_file)
        print(f"  [2] 导入 STEP OK  ({time.time()-t0:.1f}s)  title={model.GetTitle}")
        bodies = g._bodies(model)
        print(f"  [3] 体数量 = {len(bodies)}  "
              f"(实体 {len(g._solid_bodies(model))})")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '_m2_debug', 'm3_selftest.stp')
        g._save_step(model, out)
        print(f"  [4] 导出 STEP OK -> {out}  "
              f"({os.path.getsize(out)} bytes)")
        print(f"  [5] 总耗时 {time.time()-t0:.1f}s")
        print("\n  结论：COM 管线（连接/导入/取体/导出）全部可用 ✔")
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("  [X] 自检失败:", repr(e))
        return 1
    finally:
        if model is not None:
            g._close(model)


def main(argv=None):
    _force_utf8()
    p = argparse.ArgumentParser(
        description="模具生成法三：SolidWorks COM 实现法二A 的几何。")
    p.add_argument('--selftest', action='store_true', help='只验证 COM 管线')
    p.add_argument('--probe-api', action='store_true', help='探测可用 API 面')
    p.add_argument('--product', default=None, help='【产品】STP：先提形变面再生成上下模')
    p.add_argument('--upper', default=None, help='上形变面 STEP')
    p.add_argument('--lower', default=None, help='下形变面 STEP')
    p.add_argument('--output-dir', default='mold_method3', help='输出目录')
    p.add_argument('--visible', action='store_true', help='显示 SolidWorks 窗口')
    p.add_argument('--margin', type=float, default=DEFAULT_MARGIN)
    args = p.parse_args(argv)

    if args.probe_api:
        return probe_api()
    if args.selftest:
        return selftest()

    if not args.product and not args.upper and not args.lower:
        p.print_help()
        return 2
    print("法三几何尚未接入（先用 --selftest / --probe-api 验证管线）")
    return 2


if __name__ == '__main__':
    sys.exit(main())
