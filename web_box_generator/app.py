"""
三维模型查看与测量系统 - Flask 后端
升级版：使用 OpenCASCADE 引擎解析 STEP，支持切割面分析、特征检测
"""

import os
import re
import sys
import io
import uuid
import json
import time
import threading
import tempfile
import math
import multiprocessing
from collections import OrderedDict

# Ensure project root is in path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Windows 控制台默认 GBK 编码，而 mold_generator/mold_fix 等模块会打印
# ✓/→/⚠ 等 Unicode 符号 —— print 时抛 UnicodeEncodeError 会让 /api/generate_mold
# 等接口返回 500。强制 stdout/stderr 使用 UTF-8 + errors='replace'，
# 任何模块打印任何字符都不会再崩溃。
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from flask import Flask, render_template, request, send_file, jsonify, after_this_request

# Import BoxGenerator from project root
from BoxGenerator import StepBoxWriter, StlBoxWriter

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 缓存（LRU：整份网格数据几十 MB/件，必须有上限，否则内存只增不减）
_raw_stp_cache = OrderedDict()
_RAW_STP_CACHE_MAX = 8


def _cache_raw(abs_path, raw):
    _raw_stp_cache[abs_path] = raw
    while len(_raw_stp_cache) > _RAW_STP_CACHE_MAX:
        _raw_stp_cache.popitem(last=False)

# ============ 运行后自动清理中间产物 ============
# 环境变量 AUTO_CLEAN_INTERMEDIATE：'0'/'false'/'off' 关闭；其他(默认)开启。
# 开启后：每次成功生成后保留【最终 .stp】，清除可重建的中间产物——
#    ① *.geom_cache.json  几何状态缓存（mold_fix_cache/ 与 uploads/ 下所有）
#    ② *_NO_PRODUCT.stp   模具再修理的中间结果（保留 *_REFIXED.stp 最终件）
#    ③ 空的 task_id 子目录
_AUTO_CLEAN_ENABLED = os.environ.get('AUTO_CLEAN_INTERMEDIATE', '1').lower() \
    not in ('0', 'false', 'no', 'off', '')


def _cleanup_intermediates(keep_paths=None):
    """清除可重建的中间产物，保留最终 .stp。

    Args:
        keep_paths: 可选，最终交付文件路径列表（绝对/相对均可），绝不清除。

    Returns:
        {'enabled': bool, 'removed': [路径...], 'freed_b': 释放字节数}
    """
    if not _AUTO_CLEAN_ENABLED:
        return {'enabled': False, 'removed': [], 'freed_b': 0}

    keep = set()
    for p in (keep_paths or []):
        if p:
            keep.add(os.path.normcase(os.path.abspath(p)))

    removed = []
    freed = 0

    # 收集需要清理的目录（项目根 mold_fix_cache + 上传目录）
    search_roots = [os.path.join(_project_root, 'mold_fix_cache'), UPLOAD_DIR]
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, filenames in os.walk(root):
            for fn in filenames:
                f_path = os.path.join(dirpath, fn)
                if os.path.normcase(os.path.abspath(f_path)) in keep:
                    continue
                is_geom_cache = fn.lower().endswith('.geom_cache.json')
                is_refix_tmp = fn.lower().endswith('_no_product.stp')
                if is_geom_cache or is_refix_tmp:
                    try:
                        freed += os.path.getsize(f_path)
                        os.remove(f_path)
                        removed.append(f_path)
                    except OSError:
                        pass

    # 清理空目录（各 task 子目录）
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, _fns in os.walk(root, topdown=False):
            if os.path.normcase(os.path.abspath(dirpath)) == \
                    os.path.normcase(os.path.abspath(root)):
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass

    return {'enabled': True, 'removed': removed, 'freed_b': freed}


@app.route('/api/auto_clean', methods=['GET'])
def api_get_auto_clean():
    """查询当前「运行后自动清理中间产物」开关状态。"""
    return jsonify({'enabled': _AUTO_CLEAN_ENABLED})


@app.route('/api/auto_clean', methods=['POST'])
def api_set_auto_clean():
    """开启/关闭「运行后自动清理中间产物」开关。"""
    global _AUTO_CLEAN_ENABLED
    data = request.get_json(force=True)
    _AUTO_CLEAN_ENABLED = bool(data.get('enabled', True))
    return jsonify({'enabled': _AUTO_CLEAN_ENABLED})


# ============ 镜像模型 API ============

_MIRROR_RESULTS = {}  # task_id -> result

@app.route('/api/generate_mirror', methods=['POST'])
def api_generate_mirror():
    """从上传的 STEP 文件生成关于 xOy / xOz / yOz 面的镜像模型"""
    data = request.get_json(force=True)
    filepath = data.get('filepath', '')
    plane = data.get('plane', 'xOy')
    center_on_centroid = data.get('center_on_centroid', True)

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    if plane not in ('xOy', 'xOz', 'yOz'):
        return jsonify({'error': 'Invalid plane, must be xOy, xOz or yOz'}), 400

    try:
        from mirror_generator import MirrorGenerator
        gen = MirrorGenerator()
        output_dir = os.path.join(UPLOAD_DIR, 'mirror_output')
        os.makedirs(output_dir, exist_ok=True)

        # 使用剥离 uid 前缀后的干净文件名作为输出名，避免 hex 前缀
        clean_name = os.path.splitext(_strip_uid_prefix(abs_path))[0]

        result = gen.generate(
            filepath=abs_path,
            plane=plane,
            center_on_centroid=bool(center_on_centroid),
            output_dir=output_dir,
            product_name=clean_name,
        )

        task_id = uuid.uuid4().hex[:12]
        _remember(_MIRROR_RESULTS, task_id, result)
        _cleanup_intermediates([result['output_path']])

        return jsonify({
            'task_id': task_id,
            'output_path': result['output_path'],
            'plane': result['plane'],
            'plane_label': result['plane_label'],
            'centroid': result['centroid'],
            'bbox': result['bbox'],
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_mirror')
def api_download_mirror():
    """下载生成的镜像模型文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    # 验证文件来自镜像输出目录
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'mirror_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )


# ============ 旋转模型 API (绕 X/Y/Z 轴 顺/逆时针) ============

_ROTATE_RESULTS = {}  # task_id -> result

@app.route('/api/generate_rotate', methods=['POST'])
def api_generate_rotate():
    """从上传的 STEP 文件绕 X / Y / Z 轴顺时针/逆时针旋转指定角度"""
    data = request.get_json(force=True)
    filepath = data.get('filepath', '')
    axis = data.get('axis', 'z')
    angle = data.get('angle', 0.0)
    clockwise = data.get('clockwise', True)
    center_on_centroid = data.get('center_on_centroid', True)

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    if axis not in ('x', 'y', 'z'):
        return jsonify({'error': 'Invalid axis, must be x, y or z'}), 400

    try:
        angle = float(angle)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid angle, must be a number'}), 400

    try:
        from mirror_generator import MirrorGenerator
        gen = MirrorGenerator()
        output_dir = os.path.join(UPLOAD_DIR, 'mirror_output')
        os.makedirs(output_dir, exist_ok=True)

        # 使用剥离 uid 前缀后的干净文件名作为输出名，避免 hex 前缀
        clean_name = os.path.splitext(_strip_uid_prefix(abs_path))[0]

        result = gen.generate_rotation(
            filepath=abs_path,
            axis=axis,
            angle=angle,
            clockwise=bool(clockwise),
            center_on_centroid=bool(center_on_centroid),
            output_dir=output_dir,
            product_name=clean_name,
        )

        task_id = uuid.uuid4().hex[:12]
        _remember(_ROTATE_RESULTS, task_id, result)
        _cleanup_intermediates([result['output_path']])

        return jsonify({
            'task_id': task_id,
            'output_path': result['output_path'],
            'axis': result['axis'],
            'axis_label': result['axis_label'],
            'angle': result['angle'],
            'direction': result['direction'],
            'centroid': result['centroid'],
            'bbox': result['bbox'],
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_rotate')
def api_download_rotate():
    """下载生成的旋转模型文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    # 验证文件来自镜像/旋转输出目录（旋转与镜像共用 mirror_output 即可）
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'mirror_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )


# ============ 孔洞补齐 API（自动识别孔洞并补齐至平面、无超出） ============

_HOLE_FILL_RESULTS = {}  # task_id -> result

@app.route('/api/generate_hole_fill', methods=['POST'])
def api_generate_hole_fill():
    """从上传的 STEP 文件自动识别所有孔洞，并补齐至平面（无超出），导出 STEP。"""
    data = request.get_json(force=True)
    filepath = data.get('filepath', '')
    max_holes = data.get('max_holes', 1000)

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    try:
        max_holes = int(max_holes)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid max_holes'}), 400

    try:
        from hole_filler import HoleFiller
        hf = HoleFiller()
        output_dir = os.path.join(UPLOAD_DIR, 'holefill_output')
        os.makedirs(output_dir, exist_ok=True)

        clean_name = os.path.splitext(_strip_uid_prefix(abs_path))[0]

        result = hf.fill(
            filepath=abs_path,
            output_dir=output_dir,
            product_name=clean_name,
            max_holes=max_holes,
        )

        task_id = uuid.uuid4().hex[:12]
        _remember(_HOLE_FILL_RESULTS, task_id, result)
        _cleanup_intermediates([result['output_path']])

        return jsonify({
            'task_id': task_id,
            'output_path': result['output_path'],
            'holes': result['holes'],
            'hole_count': result['hole_count'],
            'filled_count': result['filled_count'],
            'skipped_count': result['skipped_count'],
            'volume_before': result['volume_before'],
            'volume_after': result['volume_after'],
            'valid': result['valid'],
            'bbox': result['bbox'],
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_hole_fill')
def api_download_hole_fill():
    """下载孔洞补齐后的模型文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'holefill_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )


# ============ 角落闭合 API (corner_close：镜像后、模具前预处理) ============

_CORNER_CLOSE_RESULTS = {}  # task_id -> result


@app.route('/api/corner_close', methods=['POST'])
def api_corner_close():
    """钣金件角落自动延伸闭合补齐。

    check_only=True 仅检测角落间隙（不修改模型）；
    否则执行修复+导出 {clean_name}_CORNER_CLOSED.stp。
    """
    data = request.get_json(force=True)
    filepath = data.get('filepath', '')
    check_only = bool(data.get('check_only', False))
    try:
        extend_mm = float(data.get('extend_mm', 6.0))
        gap_tol = float(data.get('gap_tol', 0.15))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid extend_mm or gap_tol'}), 400
    mode = data.get('mode', 'butt')
    thickness = data.get('thickness')  # None 或 float

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    try:
        from corner_close import CornerCloseRepair
        repair = CornerCloseRepair(
            extend_mm=extend_mm,
            gap_tol=gap_tol,
            thickness=float(thickness) if thickness is not None else None,
            corner_mode=mode if mode in ('sharp_closed', 'butt', 'overlap') else 'sharp_closed',
        )
        clean_name = os.path.splitext(_strip_uid_prefix(abs_path))[0]
        output_dir = os.path.join(UPLOAD_DIR, 'corner_output')
        os.makedirs(output_dir, exist_ok=True)

        if check_only:
            result = repair.check_corner_gaps(abs_path)
            return jsonify({
                'check_only': True,
                'thickness': result['thickness'],
                'total_corners': result['total_corners'],
                'closable_corners': result['closable_corners'],
                'gaps': result['gaps'],
            })

        result = repair.repair_file(
            abs_path, output_dir, output_name=f"{clean_name}_CORNER_CLOSED.stp"
        )
        task_id = uuid.uuid4().hex[:12]
        _remember(_CORNER_CLOSE_RESULTS, task_id, result)
        _cleanup_intermediates([result.get('output')])
        return jsonify({
            'task_id': task_id,
            'output_path': result.get('output'),
            'detected_corners': result.get('detected_corners', 0),
            'success_corners': result.get('success_corners_count', 0),
            'failed_corners': result.get('failed_corners_count', 0),
            'original_volume': result.get('original_volume'),
            'repaired_volume': result.get('repaired_volume'),
            'added_volume': result.get('added_volume'),
            'thickness': result.get('thickness'),
            'gap_details': result.get('gap_details', []),
            'failed_details': result.get('failed_corners', []),
            'exported': result.get('exported', False),
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_corner_close')
def api_download_corner_close():
    """下载角落闭合补齐后的产品 STEP 文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'corner_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )


# ============ 模具修复 API (补齐/挖空) ============

_FIX_RESULTS = {}  # task_id -> result

@app.route('/api/fix_mold', methods=['POST'])
def api_fix_mold():
    """模具修复：补齐(填实空腔) 或 挖空(产品贯穿孔)"""
    data = request.get_json(force=True)
    filepath = data.get('filepath', '')
    operation = data.get('operation', 'fill')  # 'fill' | 'hollow'
    product_filepath = data.get('product_filepath', None)
    remove_pillars = bool(data.get('remove_pillars', False))
    pillar_area_thresh = float(data.get('pillar_area_thresh', 500))

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    if operation not in ('fill', 'hollow'):
        return jsonify({'error': 'Invalid operation, must be fill or hollow'}), 400

    try:
        from mold_fix import MoldFixer
        fixer = MoldFixer()
        output_dir = os.path.join(UPLOAD_DIR, 'fix_output')
        os.makedirs(output_dir, exist_ok=True)

        # 使用剥离 uid 前缀后的干净文件名作为输出名，避免 hex 前缀
        clean_name = os.path.splitext(_strip_uid_prefix(abs_path))[0]

        if operation == 'fill':
            result = fixer.fill(
                filepath=abs_path,
                product_filepath=product_filepath,
                output_dir=output_dir,
                output_name=clean_name,
            )
        else:
            result = fixer.hollow(
                filepath=abs_path,
                product_filepath=product_filepath,
                output_dir=output_dir,
                output_name=clean_name,
                remove_pillars=remove_pillars,
                pillar_area_thresh=pillar_area_thresh,
            )

        task_id = uuid.uuid4().hex[:12]
        _remember(_FIX_RESULTS, task_id, result)
        _cleanup_intermediates([result['output_path']])

        return jsonify({
            'task_id': task_id,
            'output_path': result['output_path'],
            'operation': result['operation'],
            'method': result.get('method'),
            'bbox': result['bbox'],
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_fix')
def api_download_fix():
    """下载修复后的模具文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    # 验证文件来自修复输出目录
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'fix_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )


# ============ 模具再修理 API（去除产品结构体 + 凸起角落尖角补齐） ============

_REFIX_RESULTS = {}  # task_id -> result


def _normalize_verify_path(filepath, allowed_dir):
    """规范化并校验路径位于允许目录内；非法返回 None。"""
    if not filepath or not os.path.exists(filepath):
        return None
    abs_path = os.path.abspath(_normalize_path(filepath))
    if not _path_within(abs_path, os.path.abspath(allowed_dir)):
        return None
    return abs_path


@app.route('/api/mold_refix', methods=['POST'])
def api_mold_refix():
    """模具再修理：需求1 去产品结构体；需求2 上模凸起角落尖角补齐（可选，默认关闭）。

    上/下模需先经 /upload 上传得到 filepath，再连同本接口调用。
    返回两个 _REFIXED.stp 的下载路径 + 角落补齐报告（默认空）。
    """
    data = request.get_json(force=True)
    punch = _normalize_verify_path(data.get('punch_filepath', ''), UPLOAD_DIR)
    cavity = _normalize_verify_path(data.get('cavity_filepath', ''), UPLOAD_DIR)
    if punch is None or cavity is None:
        return jsonify({'error': '上模或下模文件不存在/非法，请先上传'}), 400

    corner_fill = bool(data.get('corner_fill', False))
    max_corners = int(data.get('max_corners', 4))
    wedge_depth = data.get('wedge_depth')
    wedge_depth = float(wedge_depth) if wedge_depth not in (None, '') else None

    task_id = uuid.uuid4().hex[:12]
    output_dir = os.path.join(UPLOAD_DIR, 'refix_output', task_id)
    os.makedirs(output_dir, exist_ok=True)

    try:
        from mold_refix import refix
        result = refix(
            punch_path=punch,
            cavity_path=cavity,
            output_dir=output_dir,
            corner_fill=corner_fill,
            max_corners=max_corners,
            wedge_depth=wedge_depth,
        )
    except Exception as e:
        return jsonify({'error': '再修理失败: %s' % e}), 500

    _remember(_REFIX_RESULTS, task_id, result)
    _cleanup_intermediates([result.get('punch_output'), result.get('cavity_output')])

    # 精简角落报告（去掉不可 JSON 化的 TopoDS_Shape）
    corner_report = []
    for c in (result.get('corner_report') or []):
        entry = {'index': c.get('index'), 'ok': bool(c.get('ok')),
                 'log': c.get('log', '')}
        if c.get('ok'):
            ap = c.get('apex')
            if ap:
                entry['apex'] = [round(float(v), 3) for v in ap]
            entry['wedge_vol'] = round(float(c.get('wedge_vol', 0)), 2)
            entry['added_vol'] = round(float(c.get('added_vol', 0)), 2)
        corner_report.append(entry)

    # 精简下模型腔角落凸起切除报告
    cavity_corner_report = []
    for c in (result.get('cavity_corner_report') or []):
        entry = {'index': c.get('index'), 'ok': bool(c.get('ok')),
                 'log': c.get('log', '')}
        if c.get('ok'):
            ap = c.get('apex')
            if ap:
                entry['apex'] = [round(float(v), 3) for v in ap]
            entry['wedge_vol'] = round(float(c.get('wedge_vol', 0)), 2)
            entry['removed_vol'] = round(float(c.get('removed_vol', 0)), 2)
        cavity_corner_report.append(entry)

    return jsonify({
        'task_id': task_id,
        'punch_output': result.get('punch_output'),
        'cavity_output': result.get('cavity_output'),
        'punch_no_product': result.get('punch'),
        'cavity_no_product': result.get('cavity'),
        'corner_report': corner_report,
        'cavity_corner_report': cavity_corner_report,
    })


@app.route('/api/download_refix')
def api_download_refix():
    """下载再修理后的模具文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'refix_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403
    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )



# ============ 形变配合面提取 API（凸模/凹模） ============

_CONTACT_RESULTS = {}  # task_id -> result


@app.route('/api/extract_contact_faces', methods=['POST'])
def api_extract_contact_faces():
    """根据【产品】STEP 直接提取形变配合面（调用 mold_contact_surface_extractor）。

    凸模配合面 = 产品【上表面】；凹模配合面 = 产品【下表面】。
    不再依赖生成的模具（新逻辑：形变面即产品自身的形变区域）。
    输出 *_CONTACT_FACES.stp + *.json 到 uploads/contact_output/<task_id>/，
    并返回统计摘要。
    """
    data = request.get_json(force=True)
    product_path = data.get('product_filepath', '')
    export_step = bool(data.get('export_step', True))

    def _ok(p):
        return _normalize_verify_path(p, UPLOAD_DIR)

    product = _ok(product_path)
    if product is None:
        return jsonify({'error': '请上传【产品】STEP 文件'}), 400

    task_id = uuid.uuid4().hex[:12]
    output_dir = os.path.join(UPLOAD_DIR, 'contact_output', task_id)
    os.makedirs(output_dir, exist_ok=True)

    try:
        from mold_contact_surface_extractor import MoldContactSurfaceExtractor, _stem_from
        ex = MoldContactSurfaceExtractor()
        entries = []
        summary = []
        stem = _stem_from(os.path.basename(product), '')

        # 凸模配合面 = 产品上表面
        res_punch = ex.extract_punch(product)
        step_name = f"{stem}_PUNCH_CONTACT_FACES.stp"
        json_name = f"{stem}_PUNCH_CONTACT_FACES.json"
        step_path = os.path.join(output_dir, step_name) if export_step else None
        json_path = os.path.join(output_dir, json_name)
        if step_path:
            ex.export_step(res_punch, step_path)
        ex.write_json(res_punch, json_path)
        entries.append({'kind': 'punch', 'step': step_path, 'json': json_path})
        summary.append({
            'kind': 'punch',
            'contact_face_count': res_punch['contact_face_count'],
            'total_contact_area_mm2': res_punch['total_contact_area'],
            'product_upper_count': res_punch['product_upper_count'],
            'product_lower_count': res_punch['product_lower_count'],
        })

        # 凹模配合面 = 产品下表面
        res_cavity = ex.extract_cavity(product)
        step_name_c = f"{stem}_CAVITY_CONTACT_FACES.stp"
        json_name_c = f"{stem}_CAVITY_CONTACT_FACES.json"
        step_path_c = os.path.join(output_dir, step_name_c) if export_step else None
        json_path_c = os.path.join(output_dir, json_name_c)
        if step_path_c:
            ex.export_step(res_cavity, step_path_c)
        ex.write_json(res_cavity, json_path_c)
        entries.append({'kind': 'cavity', 'step': step_path_c, 'json': json_path_c})
        summary.append({
            'kind': 'cavity',
            'contact_face_count': res_cavity['contact_face_count'],
            'total_contact_area_mm2': res_cavity['total_contact_area'],
            'product_upper_count': res_cavity['product_upper_count'],
            'product_lower_count': res_cavity['product_lower_count'],
        })

        report_path = os.path.join(output_dir, 'contact_surface_report.json')
        ex.write_combined_report([res_punch, res_cavity], report_path)
        _remember(_CONTACT_RESULTS, task_id, {'summary': summary, 'entries': entries,
                                           'report': report_path})
        _cleanup_intermediates([report_path] +
                               [e.get('step') for e in entries if e.get('step')])
        return jsonify({'task_id': task_id, 'summary': summary,
                        'entries': entries, 'report': report_path})
    except Exception as e:
        return jsonify({'error': '提取形变配合面失败: %s' % e}), 500


@app.route('/api/download_contact_faces')
def api_download_contact_faces():
    """下载提取的形变配合面 STEP / JSON 文件。"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'contact_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403
    base = _strip_uid_prefix(abs_path)
    if base.lower().endswith('.json'):
        return send_file(abs_path, mimetype='application/json',
                         as_attachment=True, download_name=base)
    return send_file(abs_path, mimetype='application/step',
                     as_attachment=True, download_name=base)


# ============ 模具生成法二 API（由形变面生成上/下模） ============

_M2_RESULTS = {}  # task_id -> result


@app.route('/api/generate_mold_method2', methods=['POST'])
def api_generate_mold_method2():
    """模具生成法二：由 上形变面/下形变面 STEP 生成上/下模。

    上模 = 上形变面每点沿 +Z 拉伸填充至高度面 P（P=最低点+h1）；
    下模 = 基座(长宽高) + 挖柱体空腔(底面=下形变面投影轮廓，高度 h_cavity) +
           下形变面垫底（最低点接触空腔底，下方填充）。
    输出 {前缀}_PUNCH_M2.stp / {前缀}_CAVITY_M2.stp + *.json 到 uploads/m2_output/<task_id>/。
    """
    data = request.get_json(force=True)
    upper = _normalize_verify_path(data.get('upper_filepath', ''), UPLOAD_DIR)
    lower = _normalize_verify_path(data.get('lower_filepath', ''), UPLOAD_DIR)
    product = _normalize_verify_path(data.get('product_filepath', ''), UPLOAD_DIR)
    if upper is None and lower is None and product is None:
        return jsonify({'error': '请上传【产品】STEP（推荐·法二B稳定单壳），或 上/下形变面 STEP（法二A）'}), 400

    def _num(k):
        try:
            return float(data[k]) if data.get(k) not in (None, '') else None
        except (TypeError, ValueError):
            return None

    h1 = _num('h1')
    h_cavity = _num('h_cavity')
    base_lx, base_ly, base_hz = _num('base_lx'), _num('base_ly'), _num('base_hz')
    try:
        margin = float(data.get('margin', 5.0))
    except (TypeError, ValueError):
        margin = 5.0
    export_step = bool(data.get('export_step', True))

    task_id = uuid.uuid4().hex[:12]
    output_dir = os.path.join(UPLOAD_DIR, 'm2_output', task_id)
    os.makedirs(output_dir, exist_ok=True)

    try:
        from mold_method2_generator import MoldMethod2Generator, _stem
        g = MoldMethod2Generator(margin=margin)
        entries = []
        summary = []

        # ── 法二B（推荐·稳定单壳有效）：基于完整产品实体 ──
        if product is not None:
            r = g.generate_from_product(product, h1=h1, h_cavity=h_cavity,
                                        base_lx=base_lx, base_ly=base_ly, base_hz=base_hz)
            if not r.get('success'):
                return jsonify({'error': '法二B(产品实体)生成失败: %s' % r.get('error')}), 500
            stem = _stem(os.path.basename(product))
            for kind in ('punch', 'cavity'):
                res = r[kind]
                sfx = 'PUNCH' if kind == 'punch' else 'CAVITY'
                step_path = (os.path.join(output_dir, f"{stem}_{sfx}_M2.stp")
                             if export_step else None)
                json_path = os.path.join(output_dir, f"{stem}_{sfx}_M2.json")
                if step_path:
                    g.export_step(res, step_path)
                g.write_json(res, json_path)
                entries.append({'kind': kind, 'step': step_path, 'json': json_path})
                summary.append({'kind': kind, 'details': res['details'],
                                'volume': res['volume']})
            _remember(_M2_RESULTS, task_id, {'summary': summary, 'entries': entries})
            return jsonify({'task_id': task_id, 'summary': summary, 'entries': entries})

        # ── 法二A：形变面路径（多壳风险，保留兼容）──
        if upper is not None:
            res = g.generate_punch(upper, h1=h1)
            if not res.get('success'):
                return jsonify({'error': '上模生成失败: %s' % res.get('error')}), 500
            stem = _stem(upper)
            step_path = (os.path.join(output_dir, f"{stem}_PUNCH_M2.stp")
                         if export_step else None)
            json_path = os.path.join(output_dir, f"{stem}_PUNCH_M2.json")
            if step_path:
                g.export_step(res, step_path)
            g.write_json(res, json_path)
            entries.append({'kind': 'punch', 'step': step_path, 'json': json_path})
            summary.append({'kind': 'punch', 'details': res['details'],
                            'volume': res['volume']})
        if lower is not None:
            res = g.generate_cavity(lower, base_lx=base_lx, base_ly=base_ly,
                                    base_hz=base_hz, h_cavity=h_cavity)
            if not res.get('success'):
                return jsonify({'error': '下模生成失败: %s' % res.get('error')}), 500
            stem = _stem(lower)
            step_path = (os.path.join(output_dir, f"{stem}_CAVITY_M2.stp")
                         if export_step else None)
            json_path = os.path.join(output_dir, f"{stem}_CAVITY_M2.json")
            if step_path:
                g.export_step(res, step_path)
            g.write_json(res, json_path)
            entries.append({'kind': 'cavity', 'step': step_path, 'json': json_path})
            summary.append({'kind': 'cavity', 'details': res['details'],
                            'volume': res['volume']})
        _remember(_M2_RESULTS, task_id, {'summary': summary, 'entries': entries})
        _cleanup_intermediates([e.get('step') for e in entries if e.get('step')])
        return jsonify({'task_id': task_id, 'summary': summary, 'entries': entries})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': '模具生成法二失败: %s' % e}), 500


@app.route('/api/download_mold_method2')
def api_download_mold_method2():
    """下载 模具生成法二 生成的上/下模 STEP / JSON。"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'm2_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403
    base = _strip_uid_prefix(abs_path)
    if base.lower().endswith('.json'):
        return send_file(abs_path, mimetype='application/json',
                         as_attachment=True, download_name=base)
    return send_file(abs_path, mimetype='application/step',
                     as_attachment=True, download_name=base)


# ============ 装配吻合结构体 API ============

_ASM_RESULTS = {}  # task_id -> result




@app.route('/api/assembly_product_mesh')
def api_assembly_product_mesh():
    """返回【装配·拼合】所需的产品结构体网格。

    关键：装配显示的产品必须与上/下模【同框架】才能正确对齐。产品不应取自
    用户上传的镜像件（另一坐标框架，Y/Z 偏置数万），而应从【原始 fix_output
    模具 compound】内自动提取同框架产品实体——与后端 build_assembly 一致。

    参数：file = 原始 fix_output 模具（PUNCH_FILLED / CAVITY_HOLLOWED）。
    """
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    try:
        from mold_refix import _extract_solids, _auto_product_index
        from step_reader import StepReader

        solids = _extract_solids(abs_path)
        idx = _auto_product_index(solids)
        if idx < 0 or idx >= len(solids):
            raise ValueError('未识别到产品实体')
        product_shape = solids[idx][1]

        # 网格化产品实体（单实体，B样条面多时控制精度）
        topo = [product_shape]
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        b = Bnd_Box()
        for s in topo:
            BRepBndLib.Add_s(s, b)
        xs = b.Get()
        size_max = max(xs[3] - xs[0], xs[4] - xs[1], xs[5] - xs[2])
        seg = 1200 if size_max <= 120 else (900 if size_max <= 300 else 700)
        linear = max(0.08, min(size_max / float(seg), 0.6))
        angular = max(0.08, min(linear * 1.2, 0.2))
        raw = StepReader(linear_deflection=linear,
                         angular_deflection=angular).read(shapes=topo)
        data = {
            'filename': os.path.basename(abs_path),
            'file_name': os.path.basename(abs_path),
            'filepath': abs_path,
            'centroid': [0, 0, 0],
            'bbox': raw.get('bbox', {}),
            'stats': raw.get('stats', {}),
            'solids': [],
        }
        for solid in raw['solids']:
            verts = solid['vertices']
            tris = solid['triangles']
            clean_verts, clean_tris = _clean_mesh(verts, tris, merge_threshold=1e-6)
            if len(clean_verts) < 9 and len(verts) >= 9:
                clean_verts, clean_tris = verts, tris
            data['solids'].append({
                'name': '产品(同框架提取)',
                'vertices': clean_verts,
                'triangles': clean_tris,
                'edges': [{'vertices': e['vertices'], 'curve_type': e.get('curve_type', 'Line')}
                          for e in solid.get('edges', [])],
            })
        total_verts = sum(len(s['vertices']) // 3 for s in data['solids'])
        total_tris = sum(len(s['triangles']) // 3 for s in data['solids'])
        data['vertex_count'] = total_verts
        data['face_count'] = total_tris
        # 质心/bbox 用产品顶点重算（与前端 computeBoundingBox 一致）
        allv = []
        for s in data['solids']:
            for j in range(0, len(s['vertices']), 3):
                allv.append([s['vertices'][j], s['vertices'][j+1], s['vertices'][j+2]])
        if allv:
            import numpy as np
            arr = np.array(allv)
            data['centroid'] = arr.mean(axis=0).tolist()
            data['bbox'] = {
                'min': arr.min(axis=0).round(4).tolist(),
                'max': arr.max(axis=0).round(4).tolist(),
                'center': ((arr.min(axis=0) + arr.max(axis=0)) / 2).round(4).tolist(),
                'size': (arr.max(axis=0) - arr.min(axis=0)).round(4).tolist(),
            }
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': '产品实体网格化失败: %s' % e}), 500



@app.route('/api/build_assembly', methods=['POST'])
def api_build_assembly():
    """输出【装配吻合结构体】：再修理后的上模 + 产品 + 再修理后的下模。

    参数（均为服务端已存在文件路径，可在 uploads/ 内）：
      punch       再修理后上模（*_REFIXED）
      cavity      再修理后下模（*_REFIXED）
      product_src 原始 fix_output 模具（PUNCH_FILLED/CAVITY_HOLLOWED，
                  内含与模具同框架的产品实体，推荐）
      product     外部产品 STEP（可选，不同框架需 align=parting）
      fuse        是否布尔融合为单一实体（默认 false）
      align       None / 'parting'
    """
    data = request.get_json(force=True)
    punch = _normalize_verify_path(data.get('punch', ''), UPLOAD_DIR)
    cavity = _normalize_verify_path(data.get('cavity', ''), UPLOAD_DIR)
    src = _normalize_verify_path(data.get('product_src', ''), UPLOAD_DIR)
    prod = _normalize_verify_path(data.get('product', ''), UPLOAD_DIR)
    if punch is None or cavity is None:
        return jsonify({'error': '上模或下模文件不存在/非法，请先再修理'}), 400

    fuse = bool(data.get('fuse', False))
    align = data.get('align') if data.get('align') in ('parting',) else None
    task_id = uuid.uuid4().hex[:12]
    output_dir = os.path.join(UPLOAD_DIR, 'assembly_output', task_id)
    os.makedirs(output_dir, exist_ok=True)

    try:
        from assembly_builder import build_assembly
        result = build_assembly(
            punch_path=punch,
            cavity_path=cavity,
            product_path=prod,
            product_src_path=src,
            output_dir=output_dir,
            fuse=fuse,
            align=align,
        )
    except Exception as e:
        return jsonify({'error': '装配结构体生成失败: %s' % e}), 500

    _remember(_ASM_RESULTS, task_id, result)
    _cleanup_intermediates([result.get('assembly')])

    # 精简报告（checks 全为数值/字符串，直接保留）
    checks = result.get('checks', {})
    return jsonify({
        'task_id': task_id,
        'assembly': result.get('assembly'),
        'fused': result.get('fused'),
        'ok': bool(result.get('ok')),
        'product_source': result.get('product_source'),
        'punch_solids': result.get('punch_solids'),
        'cavity_solids': result.get('cavity_solids'),
        'assembly_solid_count': checks.get('assembly_solid_count'),
        'assembly_volume': checks.get('assembly_volume'),
        'assembly_faces': checks.get('assembly_faces'),
        'max_gap': checks.get('max_gap'),
        'issues': result.get('issues', []),
        'elapsed_s': result.get('elapsed_s'),
    })



@app.route('/api/assembly_align', methods=['POST'])
def api_assembly_align():
    """【装配自动对齐】用 BREP 精确计算 上模/产品/下模 三件的对齐位移。

    对【任意导入、任意坐标框架】的 STEP（如产品镜像件 Z/Y 偏置数万）都能正确对齐：
    让三者分模面共面、配合中心共轴。返回每件应施加的平移 (dx,dy,dz)，前端把这些
    位移应用到 Three.js 的 mesh/group 即可实现自动对准。

    参数：punch / product / cavity = 三个 STEP 文件路径（均须在 uploads 内）。
    """
    data = request.get_json(force=True)
    punch = _normalize_verify_path(data.get('punch', ''), UPLOAD_DIR)
    product = _normalize_verify_path(data.get('product', ''), UPLOAD_DIR)
    cavity = _normalize_verify_path(data.get('cavity', ''), UPLOAD_DIR)
    if punch is None or product is None or cavity is None:
        return jsonify({'error': '上模/产品/下模文件之一不存在/非法，请先上传或生成'}), 400
    try:
        from assembly_builder import compute_align_offsets
        result = compute_align_offsets(punch, product, cavity)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': '装配自动对齐失败: %s' % e}), 500




@app.route('/api/download_assembly')
def api_download_assembly():
    """下载装配吻合结构体文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'assembly_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403
    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )



# ============ 页面路由 ============

@app.route('/')
def index():
    return render_template('index.html')

# ============ 生成立方体 ============

def _params(data):
    L = float(data.get('length', 100))
    W = float(data.get('width', 50))
    H = float(data.get('height', 30))
    fmt = data.get('format', 'stp')
    fname = data.get('filename', f'Box_{L:.0f}x{W:.0f}x{H:.0f}')
    return L, W, H, fmt, fname


@app.route('/generate', methods=['POST'])
def generate():
    try:
        data = request.get_json(force=True)
        L, W, H, fmt, fname = _params(data)
        if fmt == 'stl':
            writer = StlBoxWriter(L, W, H)
            with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as tmp:
                tmp_path = tmp.name
            writer.write(tmp_path)
            buf = io.BytesIO()
            with open(tmp_path, 'rb') as f:
                buf.write(f.read())
            os.unlink(tmp_path)
            buf.seek(0)
            return send_file(buf, mimetype='application/sla', as_attachment=True, download_name=f'{fname}.stl')
        else:
            writer = StepBoxWriter(L, W, H)
            with tempfile.NamedTemporaryFile(suffix='.stp', delete=False) as tmp:
                tmp_path = tmp.name
            writer.write(tmp_path)
            buf = io.BytesIO()
            with open(tmp_path, 'rb') as f:
                buf.write(f.read())
            os.unlink(tmp_path)
            buf.seek(0)
            return send_file(buf, mimetype='application/step', as_attachment=True, download_name=f'{fname}.stp')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============ 上传 ============

def _normalize_path(path):
    path = path.replace('\\', '/')
    return path


# 文件名安全字符白名单（中文产品号常见，保留）
_SAFE_NAME_RE = re.compile(r'[^0-9A-Za-z\u4e00-\u9fff._\-]+')


def _safe_filename(name):
    """把客户端提供的文件名净化成安全文件名（防路径穿越）。

    f.filename 由客户端完全控制，可能含 '../'、'..\\'、甚至绝对路径，
    直接 os.path.join 会写到 uploads 之外。先取 basename 再过字符白名单。
    """
    base = os.path.basename((name or '').replace('\\', '/'))
    base = _SAFE_NAME_RE.sub('_', base).strip('._')
    return base


def _path_within(child, parent):
    """判断 child（绝对路径）是否位于 parent 目录内。

    不能用 startswith：'/x/uploads_evil' 也以 '/x/uploads' 开头，
    同级目录会绕过校验。commonpath 按路径段比较，杜绝此前缀绕过。
    """
    try:
        p = os.path.abspath(parent)
        return os.path.commonpath([os.path.abspath(child), p]) == p
    except ValueError:
        return False


def _remember(store, tid, val, keep=64):
    """任务结果登记 + 限量留存（超过 keep 淘汰最旧，防止字典无限增长）。"""
    store[tid] = val
    while len(store) > keep:
        store.pop(next(iter(store)))


def _strip_uid_prefix(filename):
    """从文件名中移除用于唯一存储的 {12位hex}_ 前缀，返回干净的文件名。

    上传时文件以 f'{uid}_{原文件名}' 存储，uid 是 uuid4().hex[:12]，
    这里循环剥离所有连续的前缀（防止多重上传累积多个 hex 前缀），
    避免 hex 前缀传播到镜像/模具等生成文件名中。

    Args:
        filename: 文件路径或文件名

    Returns:
        去掉所有 {12位hex}_ 前缀后的基本文件名；若无前缀则原样返回
    """
    base = os.path.basename(filename)
    while True:
        parts = base.split('_', 1)
        if len(parts) == 2 and len(parts[0]) == 12 and all(c in '0123456789abcdef' for c in parts[0].lower()):
            # UUID hex 前缀几乎总包含 a-f 字母；全数字的 12 位前缀可能是
            # 正常产品编号，不应剥离
            if any(c in 'abcdef' for c in parts[0].lower()):
                base = parts[1]
                continue
        break
    return base


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.stp', '.step', '.stl'):
        return jsonify({'error': 'Unsupported format'}), 400
    # ★文件名必须净化★：f.filename 由客户端控制，可含 '../'、'..\\'、绝对路径，
    # 直接 join 会写出 uploads 目录（路径穿越任意文件写）。
    safe_name = _safe_filename(f.filename)
    if not safe_name:
        return jsonify({'error': 'Invalid filename'}), 400
    uid = uuid.uuid4().hex[:12]
    save_name = f'{uid}_{safe_name}'
    save_path = os.path.join(UPLOAD_DIR, save_name)
    # 防御性校验：最终路径必须落在 UPLOAD_DIR 内
    if not _path_within(save_path, UPLOAD_DIR):
        return jsonify({'error': 'Invalid filename'}), 400
    f.save(save_path)

    abs_path = os.path.abspath(save_path)

    if ext in ('.stp', '.step'):
        data = _parse_step(abs_path)
    else:
        data = _parse_stl(abs_path)

    # 剥离存储用的 uid 前缀，避免 hex 前缀传播到界面和生成文件名
    clean_name = _strip_uid_prefix(f.filename)
    data['filename'] = clean_name
    data['file_name'] = clean_name
    data['filepath'] = abs_path
    return jsonify(data)


@app.route('/api/mesh')
def api_mesh():
    """按文件路径返回该文件的三角网格数据（与 /upload 返回结构一致）。

    用于“装配·拼合”模块：直接加载服务器上已生成的模具/产品 STEP 网格，
    避免重新上传。仅允许访问 uploads 目录内的文件。
    """
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    ext = os.path.splitext(abs_path)[1].lower()
    try:
        if ext in ('.stp', '.step'):
            data = _parse_step(abs_path)
        elif ext == '.stl':
            data = _parse_stl(abs_path)
        else:
            return jsonify({'error': 'Unsupported format'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    clean_name = _strip_uid_prefix(os.path.basename(abs_path))
    data['filename'] = clean_name
    data['file_name'] = clean_name
    data['filepath'] = abs_path
    return jsonify(data)


def _clean_mesh(vertices, triangles, merge_threshold=1e-6):
    """
    清理网格数据：去除重复顶点、移除退化三角形
    
    Args:
        vertices: 顶点列表 [v0x, v0y, v0z, v1x, v1y, v1z, ...]
        triangles: 三角形索引列表 [i0, i1, i2, i3, i4, i5, ...]
        merge_threshold: 顶点合并距离阈值
    
    Returns:
        (new_vertices, new_triangles)
    """
    if len(vertices) < 9:
        return vertices, triangles
    
    # 1. 合并重复顶点
    vert_list = [(vertices[i], vertices[i+1], vertices[i+2]) for i in range(0, len(vertices), 3)]
    
    # 用哈希表快速去重
    vert_map = {}
    new_verts = []
    index_map = []
    
    for v in vert_list:
        # 超高精度容差 0.0000001mm（1e-7）：只合并坐标完全一致的顶点
        # OCCT 相邻面共享边上的节点坐标完全一致，合并后曲面无缝；
        # 不同面的边界顶点哪怕差 0.00001mm 也不合并，避免缝合处法线平均鼓包
        key = (round(v[0] * 1e7) / 1e7,
               round(v[1] * 1e7) / 1e7,
               round(v[2] * 1e7) / 1e7)
        existing = vert_map.get(key)
        if existing is not None:
            index_map.append(existing)
        else:
            idx = len(new_verts)
            vert_map[key] = idx
            new_verts.append(v)
            index_map.append(idx)
    
    # 2. 重建三角形并移除退化三角形
    new_tris = []
    for i in range(0, len(triangles), 3):
        i0 = index_map[triangles[i]]
        i1 = index_map[triangles[i+1]]
        i2 = index_map[triangles[i+2]]
        
        # 跳过退化三角形（两个或三个顶点相同）
        if i0 == i1 or i1 == i2 or i0 == i2:
            continue
        
        new_tris.extend([i0, i1, i2])
    
    # 展平顶点
    flat_verts = []
    for v in new_verts:
        flat_verts.extend(v)
    
    return flat_verts, new_tris


_PARSE_TIMEOUT = 120  # 秒；超过则强制终止解析子进程，防止页面无限加载（复杂大模型导入一次约 20-30s，留足余量）


def _parse_error_data(filepath, message):
    """生成解析失败/超时的标准返回结构（前端识别 error 字段后停止转圈）"""
    return {
        'filename': os.path.basename(filepath),
        'filepath': os.path.abspath(filepath),
        'error': message,
        'solids': [],
        'bbox': {'min': [0, 0, 0], 'max': [0, 0, 0], 'center': [0, 0, 0], 'size': [0, 0, 0]},
        'centroid': [0, 0, 0],
    }


_pool = None


def _get_parse_pool():
    """懒加载单工作进程池。

    关键原因：OpenCASCADE 的网格化（BRepMesh）是原生 C++ 代码，阻塞期间
    Python 解释器拿不到控制权 —— 这就是"解析卡住时页面一直转圈、终端
    Ctrl+C 也无效"的根本原因。把解析放进独立常驻子进程后：
      - 主进程永不阻塞，Ctrl+C 可立即响应；
      - 解析超时可被主进程强制终止，页面不再无限加载。
    """
    global _pool
    if _pool is None:
        _pool = multiprocessing.get_context('spawn').Pool(1)
    return _pool


def _reset_parse_pool():
    """终止异常/超时的解析子进程池（下次调用自动重建）"""
    global _pool
    if _pool is not None:
        try:
            _pool.terminate()
            _pool.join()
        except Exception:
            pass
        _pool = None


def _parse_step_worker(filepath):
    """在子进程中执行自适应精度的 STEP 解析，返回 raw 数据字典。

    优化（解决复杂大模型解析超时）：
      主导耗时是 STEP 导入（B 样条面多的模型可达 20s+），网格化本身很快。
      因此在子进程内只\"导入一次\"：
        1. 用 BRepBndLib 快速求包围盒估计尺寸 L（不网格化，极快）；
        2. 据 L 自适应选定线性/角度偏差，对同一 shape 网格化一次；
        3. 三角形超限时，再在\"同一 shape\"上降精度重新网格化（不重新导入）。
      即由\"多遍全量导入+网格化\"优化为\"一遍导入+1~2 次同形状网格化\"，
      大幅降低单个复杂模型的解析耗时，避免 60s 超时。
    """
    try:
        from step_reader import StepReader, import_shapes, topo_bbox

        # 第一遍：只导入一次（不网格化），返回 topo shapes
        topo = import_shapes(filepath)

        # 用 BRepBndLib 快速估计模型最大尺寸 L
        try:
            bb = topo_bbox(topo)
            size = [bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]]
            size_max = max(size) if size else 0.0
        except Exception:
            size_max = 0.0
        if size_max <= 0:
            # 拿不到尺寸：退化为粗精度直接读取（仍只导入一次）
            return StepReader(linear_deflection=2.0, angular_deflection=0.5).read(shapes=topo)

        # 自适应精度：小零件细、大零件粗（大模具曲面交线区会自然加密，不能太细）
        if size_max <= 120:
            segments = 1200          # 小零件：细分密一点，细节清晰
        elif size_max <= 300:
            segments = 900           # 中型：兼顾细节与速度
        else:
            segments = 700           # 大模具：防三角形爆炸
        linear = max(0.08, min(size_max / float(segments), 0.6))
        angular = max(0.08, min(linear * 1.2, 0.2))

        # 第二遍：按选定精度对同一 shape 网格化（不重新导入）
        raw = StepReader(linear_deflection=linear,
                         angular_deflection=angular).read(shapes=topo)

        # 三角形数量上限保护：超限时最多 2 轮降精度，在同一 shape 上重新网格化
        MAX_TRIANGLES = 200000
        tris = sum(s['triangle_count'] for s in raw['solids'])
        guard = 0
        while tris > MAX_TRIANGLES and guard < 2:
            guard += 1
            coarser = max(linear * 1.7, size_max / 700.0)
            coarser = min(coarser, 1.2)
            coarser_ang = min(coarser * 1.3, 0.25)
            try:
                raw2 = StepReader(linear_deflection=coarser,
                                  angular_deflection=coarser_ang).read(shapes=topo)
                tris2 = sum(s['triangle_count'] for s in raw2['solids'])
                if tris2 >= tris:
                    break  # 降精度无收益，停止
                linear, angular = coarser, coarser_ang
                raw, tris = raw2, tris2
            except Exception:
                break

        raw['_parsed_linear'] = linear
        raw['_parsed_angular'] = angular
        return raw

    except Exception as e:
        return {'error': f'解析进程异常: {e}'}


def _parse_step(filepath):
    """使用 OpenCASCADE 引擎解析 STEP 文件。

    在独立子进程中执行：
      1. 自适应网格精度，避免固定 0.005mm 超高精度生成上亿三角形；
      2. 超过 _PARSE_TIMEOUT 秒自动强制终止，页面不再无限加载；
      3. 解析卡在原生代码时，主进程仍能响应 Ctrl+C。
    """
    abs_path = os.path.abspath(filepath)
    pool = _get_parse_pool()

    try:
        result = pool.apply_async(_parse_step_worker, (abs_path,))
        payload = result.get(timeout=_PARSE_TIMEOUT)
    except Exception as e:
        name = type(e).__name__
        print(f"[parse] {name}: {e}")
        _reset_parse_pool()
        if 'Timeout' in name:
            return _parse_error_data(
                abs_path,
                f'解析超时（超过 {_PARSE_TIMEOUT} 秒），已自动终止。'
                '该 STEP 模型过于复杂，建议：1) 在 CAD 中精简模型后重新上传；'
                '2) 导出为 STL 格式上传。'
            )
        return _parse_error_data(abs_path, f'解析进程异常: {e}')

    if isinstance(payload, dict) and payload.get('error'):
        _reset_parse_pool()
        return _parse_error_data(abs_path, payload['error'])

    raw = payload
    _cache_raw(abs_path, raw)
    
    # 收集所有顶点
    all_verts = []
    for solid in raw['solids']:
        verts = solid.get('vertices', [])
        all_verts.extend([verts[i:i+3] for i in range(0, len(verts), 3)])

    # 计算质心
    cx = sum(v[0] for v in all_verts) / len(all_verts) if all_verts else 0
    cy = sum(v[1] for v in all_verts) / len(all_verts) if all_verts else 0
    cz = sum(v[2] for v in all_verts) / len(all_verts) if all_verts else 0
    centroid = [cx, cy, cz]

    bbox = raw.get('bbox', {})

    data = {
        'filename': raw.get('filename', os.path.basename(filepath)),
        'file_name': raw.get('filename', os.path.basename(filepath)),
        'filepath': os.path.abspath(filepath),
        'centroid': centroid,
        'bbox': bbox,
        'stats': raw.get('stats', {}),
        'solids': [],
    }
    for solid in raw['solids']:
        verts = solid['vertices']
        tris = solid['triangles']
        
        # 清理网格数据：去重、去除退化三角形
        # 阈值使用 1e-6：仅合并真正重复的顶点，避免 1e-4 强制网格取整
        # 导致 CAD 布尔窄边被塌陷成畸形三角形（突起/锯齿）
        clean_verts, clean_tris = _clean_mesh(verts, tris, merge_threshold=1e-6)
        
        # 如果去重后顶点太少，说明原网格有问题，保留原始数据
        if len(clean_verts) < 9 and len(verts) >= 9:
            clean_verts, clean_tris = verts, tris
        
        data['solids'].append({
            'name': solid.get('name', 'Shape'),
            'vertices': clean_verts,
            'triangles': clean_tris,
            'edges': [{'vertices': e['vertices'], 'curve_type': e.get('curve_type', 'Line')}
                      for e in solid.get('edges', [])],
        })
    
    total_verts = sum(len(s['vertices']) // 3 for s in data['solids'])
    total_tris = sum(len(s['triangles']) // 3 for s in data['solids'])
    data['vertex_count'] = total_verts
    data['face_count'] = total_tris
    
    return data


def _compute_raw_centroid(raw):
    """Compute centroid from raw (uncentered) vertex data"""
    all_verts = []
    for solid in raw.get('solids', []):
        verts = solid.get('vertices', [])
        for i in range(0, len(verts), 3):
            all_verts.append([verts[i], verts[i+1], verts[i+2]])
    if not all_verts:
        return [0, 0, 0]
    cx = sum(v[0] for v in all_verts) / len(all_verts)
    cy = sum(v[1] for v in all_verts) / len(all_verts)
    cz = sum(v[2] for v in all_verts) / len(all_verts)
    return [cx, cy, cz]


def _parse_stl(filepath):
    """Parse binary or ASCII STL file"""
    # Try to detect and parse
    with open(filepath, 'rb') as f:
        header = f.read(80)
        f.seek(0)
        content = f.read()

    # Check if it's binary STL (look for "solid" at start in ASCII)
    is_ascii = content[:5].lower() == b'solid' and b'\n' in content[:80]

    if is_ascii:
        return _parse_ascii_stl(filepath)
    else:
        return _parse_binary_stl(filepath)


def _parse_binary_stl(filepath):
    import struct
    with open(filepath, 'rb') as f:
        header = f.read(80)
        num_triangles = struct.unpack('<I', f.read(4))[0]

    vert_map = {}
    unique_verts = []
    faces = []

    with open(filepath, 'rb') as f:
        f.read(80)  # skip header
        num_triangles = struct.unpack('<I', f.read(4))[0]
        for _ in range(num_triangles):
            f.read(12)  # skip normal
            face_verts = []
            for _ in range(3):
                x, y, z = struct.unpack('<fff', f.read(12))
                key = (round(x, 5), round(y, 5), round(z, 5))
                if key not in vert_map:
                    vert_map[key] = len(unique_verts)
                    unique_verts.append([x, y, z])
                face_verts.append(vert_map[key])
            faces.append(face_verts)
            f.read(2)  # attribute byte count

    edges = _build_edges(faces)
    unique_verts = _center_verts(unique_verts)
    bbox = _calc_bbox(unique_verts)
    return {
        'solids': [{'vertices': [v for pt in unique_verts for v in pt],
                     'triangles': [idx for f in faces for idx in f],
                     'edges': []}],
        'bbox': bbox,
        'stats': {'solids': 1, 'faces': len(faces), 'edges': len(edges), 'triangles': len(faces),
                   'volume': 0, 'surface_area': 0},
        'vertex_count': len(unique_verts),
        'face_count': len(faces),
        'filename': os.path.basename(filepath),
    }


def _parse_ascii_stl(filepath):
    import re
    with open(filepath, 'r') as f:
        content = f.read()
    vert_map = {}
    unique_verts = []
    faces = []
    pattern = r'vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)'
    matches = list(re.finditer(pattern, content, re.IGNORECASE))
    for i in range(0, len(matches), 3):
        if i + 2 >= len(matches):
            break
        face_verts = []
        for j in range(3):
            x, y, z = float(matches[i+j].group(1)), float(matches[i+j].group(2)), float(matches[i+j].group(3))
            key = (round(x, 5), round(y, 5), round(z, 5))
            if key not in vert_map:
                vert_map[key] = len(unique_verts)
                unique_verts.append([x, y, z])
            face_verts.append(vert_map[key])
        faces.append(face_verts)
    edges = _build_edges(faces)
    unique_verts = _center_verts(unique_verts)
    bbox = _calc_bbox(unique_verts)
    return {
        'solids': [{'vertices': [v for pt in unique_verts for v in pt],
                     'triangles': [idx for f in faces for idx in f],
                     'edges': []}],
        'bbox': bbox,
        'stats': {'solids': 1, 'faces': len(faces), 'edges': len(edges), 'triangles': len(faces),
                   'volume': 0, 'surface_area': 0},
        'vertex_count': len(unique_verts),
        'face_count': len(faces),
        'filename': os.path.basename(filepath),
    }


def _build_edges(faces):
    edge_set = set()
    edges_out = []
    for f in faces:
        for i in range(3):
            a, b = f[i], f[(i + 1) % 3]
            key = (a, b) if a < b else (b, a)
            if key not in edge_set:
                edge_set.add(key)
                edges_out.append([key[0], key[1]])
    return edges_out


def _center_verts(verts):
    if not verts:
        return verts
    cx = sum(v[0] for v in verts) / len(verts)
    cy = sum(v[1] for v in verts) / len(verts)
    cz = sum(v[2] for v in verts) / len(verts)
    for v in verts:
        v[0] -= cx
        v[1] -= cy
        v[2] -= cz
    return verts


def _calc_bbox(verts):
    if not verts:
        return {'min': [0, 0, 0], 'max': [0, 0, 0], 'center': [0, 0, 0], 'size': [0, 0, 0]}
    arr = list(zip(*verts))
    bmin = [min(arr[0]), min(arr[1]), min(arr[2])]
    bmax = [max(arr[0]), max(arr[1]), max(arr[2])]
    return {
        'min': [round(v, 4) for v in bmin],
        'max': [round(v, 4) for v in bmax],
        'center': [round((bmin[i] + bmax[i]) / 2, 4) for i in range(3)],
        'size': [round(bmax[i] - bmin[i], 4) for i in range(3)],
    }


# ============ 切割面分析 API ============


@app.route('/api/slice_plane')
def api_slice_plane():
    """计算平面与模型相交轮廓"""
    filepath = _normalize_path(request.args.get('file', ''))
    origin_str = request.args.get('origin', '')
    normal_str = request.args.get('normal', '')

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [
        os.path.abspath(UPLOAD_DIR),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '三维数模')),
    ]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    try:
        origin = tuple(float(v.strip()) for v in origin_str.split(','))
        normal = tuple(float(v.strip()) for v in normal_str.split(','))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid origin or normal format'}), 400

    if len(origin) != 3 or len(normal) != 3:
        return jsonify({'error': 'origin and normal must have exactly 3 components'}), 400

    # 缓存加载
    if abs_path in _raw_stp_cache:
        raw = _raw_stp_cache[abs_path]
    else:
        from step_reader import StepReader
        reader = StepReader(linear_deflection=0.5, angular_deflection=0.3)
        raw = reader.read(abs_path)
        _cache_raw(abs_path, raw)

    # 计算原始坐标系的质心
    raw_centroid = _compute_raw_centroid(raw)

    # 前端传过来的 origin 是在居中的坐标系（已减去质心）中的坐标，
    # 需要转换回原始坐标系才能与 raw 数据匹配
    raw_origin = (
        origin[0] + raw_centroid[0],
        origin[1] + raw_centroid[1],
        origin[2] + raw_centroid[2],
    )

    from slice_analyzer import MeshSlicer
    slicer = MeshSlicer(direction=normal, origin=raw_origin)
    slicer.add_solids_from_raw(raw)
    result = slicer.intersect_plane(plane_origin=raw_origin, plane_normal=normal)

    # 补充包围盒信息（保持在原始坐标系中）
    bbox = raw.get('bbox', {})
    result['bbox'] = {
        'min': bbox.get('min', [0, 0, 0]),
        'max': bbox.get('max', [0, 0, 0]),
        'size': bbox.get('size', [0, 0, 0]),
        'center': [
            (bbox['min'][i] + bbox['max'][i]) / 2
            for i in range(3)
        ] if bbox.get('min') and bbox.get('max') else [0, 0, 0],
    }

    return jsonify(result)


# ============ 批量切片 API ============


@app.route('/api/slice_plane_batch')
def api_slice_plane_batch():
    """批量预计算切片"""
    filepath = _normalize_path(request.args.get('file', ''))
    normal_str = request.args.get('normal', '')
    range_min_str = request.args.get('range_min', '')
    range_max_str = request.args.get('range_max', '')
    count_str = request.args.get('count', '100')

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [
        os.path.abspath(UPLOAD_DIR),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '三维数模')),
    ]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    try:
        normal = tuple(float(v.strip()) for v in normal_str.split(','))
        range_min = float(range_min_str)
        range_max = float(range_max_str)
        count = int(count_str)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    if len(normal) != 3 or count < 2 or count > 500:
        return jsonify({'error': 'Invalid parameters'}), 400

    # 缓存加载
    if abs_path in _raw_stp_cache:
        raw = _raw_stp_cache[abs_path]
    else:
        from step_reader import StepReader
        reader = StepReader(linear_deflection=0.5, angular_deflection=0.3)
        raw = reader.read(abs_path)
        _cache_raw(abs_path, raw)

    # 计算原始坐标系质心
    raw_centroid = _compute_raw_centroid(raw)

    bbox = raw.get('bbox', {})
    bbox_min = bbox.get('min', [0, 0, 0])
    bbox_max = bbox.get('max', [0, 0, 0])
    center = [(bbox_min[i] + bbox_max[i]) / 2 for i in range(3)]

    from slice_analyzer import MeshSlicer
    slicer = MeshSlicer(direction=normal, origin=tuple(center))
    slicer.add_solids_from_raw(raw)

    slices = []
    step = (range_max - range_min) / (count - 1)
    for i in range(count):
        depth = range_min + i * step
        origin = list(center)
        # 沿着法线方向偏移
        max_axis = max(range(3), key=lambda k: abs(normal[k]))
        origin[max_axis] = depth
        origin = tuple(origin)
        try:
            res = slicer.intersect_plane(plane_origin=origin, plane_normal=normal)
            slices.append({
                'depth': round(depth, 3),
                'best_length': res.get('best_length', 0),
                'arc_lengths': res.get('arc_lengths', []),
                'contours_3d': res.get('contours_3d', []),
            })
        except Exception:
            slices.append({
                'depth': round(depth, 3),
                'best_length': 0,
                'arc_lengths': [],
                'contours_3d': [],
            })

    result = {
        'normal': list(normal),
        'range': [range_min, range_max],
        'count': count,
        'bbox': {
            'min': bbox_min,
            'max': bbox_max,
            'size': bbox.get('size', [0, 0, 0]),
            'center': center,
        },
        'slices': slices,
    }
    return jsonify(result)


# ============ 模具生成 API ============

_MOLD_RESULTS = {}  # task_id -> result

@app.route('/api/generate_mold', methods=['POST'])
def api_generate_mold():
    """从上传的 STEP 文件自动生成上下模具（凹模 + 凸模）"""
    data = request.get_json(force=True)
    filepath = data.get('filepath', '')
    try:
        side_margin = float(data.get('side_margin', 10))
        base_thickness = float(data.get('base_thickness', 10))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid margin or thickness'}), 400

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [os.path.abspath(UPLOAD_DIR)]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    try:
        from mold_generator import MoldGenerator
        gen = MoldGenerator()
        output_dir = os.path.join(UPLOAD_DIR, 'mold_output')
        os.makedirs(output_dir, exist_ok=True)

        # 使用剥离 uid 前缀后的干净文件名作为输出名，避免 hex 前缀
        clean_name = os.path.splitext(_strip_uid_prefix(abs_path))[0]

        result = gen.generate(
            filepath=abs_path,
            side_margin=side_margin,
            base_thickness=base_thickness,
            output_dir=output_dir,
            product_name=clean_name,
        )

        task_id = uuid.uuid4().hex[:12]
        _remember(_MOLD_RESULTS, task_id, result)
        _cleanup_intermediates([result.get('cavity'), result.get('punch')])

        info = result.get('info', {})
        return jsonify({
            'task_id': task_id,
            'cavity': result.get('cavity'),
            'punch': result.get('punch'),
            'info': info,
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_mold')
def api_download_mold():
    """下载生成的模具文件"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    # 验证文件来自模具输出目录
    allowed_dirs = [os.path.abspath(os.path.join(UPLOAD_DIR, 'mold_output'))]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    return send_file(
        abs_path,
        mimetype='application/step',
        as_attachment=True,
        download_name=_strip_uid_prefix(abs_path),
    )


# ============ 模型特征分析 API ============

@app.route('/api/model_features')
def api_model_features():
    """检测模型特征：折弯/圆孔/凹槽等"""
    filepath = _normalize_path(request.args.get('file', ''))
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.abspath(filepath)
    allowed_dirs = [
        os.path.abspath(UPLOAD_DIR),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '三维数模')),
    ]
    if not any(_path_within(abs_path, d) for d in allowed_dirs):
        return jsonify({'error': 'Access denied'}), 403

    # 缓存加载原始数据
    if abs_path in _raw_stp_cache:
        raw = _raw_stp_cache[abs_path]
    else:
        from step_reader import StepReader
        reader = StepReader(linear_deflection=0.5, angular_deflection=0.3)
        raw = reader.read(abs_path)
        _cache_raw(abs_path, raw)

    # 使用 bend_detector 和 brep_topology 进行特征分析
    from brep_topology import BrepTopologyExtractor
    from bend_detector import BendDetector

    try:
        extractor = BrepTopologyExtractor(abs_path)
        topo = extractor.extract()

        detector = BendDetector(topo)
        bend_result = detector.detect()

        classification = topo.get('classification', {})
        faces = topo.get('faces', [])
        thickness = topo.get('thickness', 2.0)
        bbox = topo.get('bbox', {})
        bbox_size = bbox.get('size', [0, 0, 0]) if bbox else [0, 0, 0]

        bends = bend_result.get('bends', [])
        has_bends = len(bends) > 0

        # 圆孔检测
        planar_indices = classification.get('planar_faces', [])
        fillet_indices = {f['face_idx'] for f in classification.get('fillet_faces', [])}
        cylindrical_indices = classification.get('cylindrical_faces', [])

        main_normal = None
        max_area = -1
        for fi in planar_indices:
            f = faces[fi]
            area = f.get('area', 0)
            if area > max_area:
                max_area = area
                sf = f.get('surface', {})
                main_normal = sf.get('normal')

        holes = []
        for fi in cylindrical_indices:
            if fi in fillet_indices:
                continue
            f = faces[fi]
            sp = f.get('surface_params', {})
            radius = sp.get('radius')
            if radius is None:
                continue
            cyl_axis = sp.get('axis_direction')

            # 检查垂直方向
            is_vertical = False
            if main_normal and cyl_axis:
                dot = abs(main_normal[0] * cyl_axis[0] + main_normal[1] * cyl_axis[1] + main_normal[2] * cyl_axis[2])
                if dot > 0.9:
                    is_vertical = True

            if radius < 0.5:
                continue
            holes.append({
                'face_index': fi,
                'radius': round(radius, 2),
                'diameter': round(radius * 2, 2),
                'is_through_hole': True,
                'is_vertical': is_vertical,
            })

        # 折弯检测结果
        bend_features = []
        for b in bends:
            bend_features.append({
                'angle': round(b.get('angle', 0), 1),
                'radius': round(b.get('radius', 0), 2),
                'length': round(b.get('length', 0), 2),
                'edge_index': b.get('edge_index', -1),
                'type': b.get('type', 'bend'),
            })

        result = {
            'has_bends': has_bends,
            'bend_count': len(bend_features),
            'bends': bend_features,
            'holes': holes,
            'hole_count': len(holes),
            'thickness': round(thickness, 2),
            'bbox_size': [round(s, 2) for s in bbox_size],
            'classification': {
                'total_faces': len(faces),
                'planar_faces': len(planar_indices),
                'cylindrical_faces': len(cylindrical_indices),
                'fillet_faces': len(fillet_indices),
            },
        }
        return jsonify(result)

    except Exception as e:
        return jsonify({
            'error': str(e),
            'has_bends': False,
            'bend_count': 0,
            'bends': [],
            'holes': [],
            'hole_count': 0,
            'thickness': 0,
            'bbox_size': [0, 0, 0],
            'classification': {},
        }), 500


# ============================================================================
# 模具自动生成（moldkit 多方法）—— 把 moldkit 的方法注册表接到本界面
# ----------------------------------------------------------------------------
# 说明：这一整段是【追加】的，不修改上面任何现有接口。
#   /api/moldkit/methods      列出所有方法（含参数声明）
#   /api/moldkit/run          选方法跑（后台线程 + 轮询，避免几十秒卡死浏览器）
#   /api/moldkit/job/<id>     查询进度/日志/结果
#   /api/moldkit/file/<id>/.. 下载产物
# ============================================================================
try:
    from moldkit import pipeline as _mk_pipeline, registry as _mk_registry
    _MOLDKIT_OK = True
    _MOLDKIT_ERR = ''
except Exception as _mk_e:          # 缺依赖/语法错误都不该拖垮主站
    _MOLDKIT_OK = False
    _MOLDKIT_ERR = str(_mk_e)

_MK_JOBS = {}
_MK_LOCK = threading.Lock()
_MK_WORKROOT = os.path.join(_project_root, 'mold_jobs')
_MK_UPLOADS = os.path.join(_MK_WORKROOT, '_uploads')


class _MkJob:
    def __init__(self, jid, methods, params, files, part_name):
        self.id = jid
        self.methods = methods
        self.params = params
        self.files = files
        self.part_name = part_name
        self.log = []
        self.status = 'running'
        self.results = {}
        self.error = None
        self.t0 = time.time()
        self.workdir = os.path.join(_MK_WORKROOT, jid)
        os.makedirs(self.workdir, exist_ok=True)

    def say(self, s=''):
        with _MK_LOCK:
            self.log.append(str(s))
            if len(self.log) > 4000:
                del self.log[:1000]

    def snap(self):
        with _MK_LOCK:
            log = list(self.log)
        arts = []
        for f in sorted(os.listdir(self.workdir)):
            p = os.path.join(self.workdir, f)
            if os.path.isfile(p):
                arts.append({'name': f, 'size': os.path.getsize(p)})
        return {'id': self.id, 'status': self.status, 'log': log[-400:],
                'elapsed': round(time.time() - self.t0, 1),
                'results': self.results, 'error': self.error, 'artifacts': arts}


def _mk_worker(job):
    try:
        # ★先自检/自愈 SolidWorks 引擎★
        #   这台机器上 SW 的 VBE 不落盘（Ctrl+S 不写 .swp），引擎只在 SW 内存里，
        #   SW 一重启就没了。跑之前自检一次，缺了就自动重装，用户不用管。
        try:
            from moldkit.swjobs import ensure_engine
            ok, msg = ensure_engine(log=job.say)
            job.say("[engine] %s" % msg)
            if not ok:
                job.error = msg
                job.status = 'error'
                return
        except Exception as e:
            job.say("[engine] 自检异常（继续尝试运行）: %r" % (e,))

        ctx = _mk_pipeline.make_context(
            job.workdir, product=job.files.get('product'),
            upper=job.files.get('upper'), lower=job.files.get('lower'),
            params=job.params, part_name=job.part_name, log=job.say)
        job.say('工作目录: %s' % job.workdir)
        job.say('零件名  : %s' % job.part_name)
        if len(job.methods) == 1:
            out = {job.methods[0]: _mk_pipeline.run(job.methods[0], ctx, log=job.say)}
        else:
            out = _mk_pipeline.run_all(ctx, only=job.methods, log=job.say)
        _mk_pipeline.summarize(out, log=job.say)
        _mk_pipeline.save_report(
            out, os.path.join(job.workdir, '%s_report.json' % job.part_name))
        job.results = {k: v.to_dict() for k, v in out.items()}
        job.status = 'done'
    except Exception as e:
        import traceback
        job.error = str(e)
        job.say(traceback.format_exc())
        job.status = 'error'


@app.route('/api/moldkit/methods')
def api_mk_methods():
    if not _MOLDKIT_OK:
        return jsonify({'error': 'moldkit 不可用: %s' % _MOLDKIT_ERR, 'methods': []})
    out = []
    for info in _mk_registry.list_info():
        try:
            ok, why = _mk_registry.get(info['name']).available()
            info['available'] = ok
            info['unavailable_reason'] = why
        except Exception as e:
            info['available'] = False
            info['unavailable_reason'] = str(e)
        out.append(info)
    return jsonify({'methods': out})


@app.route('/api/moldkit/run', methods=['POST'])
def api_mk_run():
    if not _MOLDKIT_OK:
        return jsonify({'error': 'moldkit 不可用: %s' % _MOLDKIT_ERR}), 500
    os.makedirs(_MK_UPLOADS, exist_ok=True)
    form = request.form
    methods = form.getlist('methods') or []
    if not methods:
        return jsonify({'error': '没有选择方法'}), 400
    params = {}
    for k, v in form.items():
        if k.startswith('p.'):
            params[k[2:]] = v
    # 上传文件（产品/上形变面/下形变面 + 方法声明的额外输入）
    extra_keys = set()
    for info in _mk_registry.list_info():
        for x in info.get('extra_inputs', []):
            extra_keys.add(x['key'])

    # ★文件名要干净★
    #   原来存成 `<8位16进制>_原文件名`，结果产物变成
    #   `092b28911a59_YA-1131-505_MIRROR_xOy_m3_PUNCH.stp` —— 又长又带一串 16 进制。
    #   现在：上传统一放进本次任务的子目录（靠目录隔离，不再靠前缀防重名），
    #         文件名保持原样；零件名再过一遍 clean_part_name 剥掉历史前缀。
    jid = uuid.uuid4().hex[:10]
    job_dir = os.path.join(_MK_WORKROOT, jid)
    up_dir = os.path.join(job_dir, '_in')
    os.makedirs(up_dir, exist_ok=True)

    def _save(f):
        try:
            from moldkit.pipeline import clean_part_name as _clean
        except Exception:
            _clean = lambda x: os.path.basename(x)
        base = os.path.basename(f.filename)
        stem, ext = os.path.splitext(base)
        dst = os.path.join(up_dir, '%s%s' % (_clean(stem) or 'in', ext or '.stp'))
        n = 1
        while os.path.exists(dst):
            dst = os.path.join(up_dir, '%s_%d%s' % (_clean(stem) or 'in', n, ext or '.stp'))
            n += 1
        f.save(dst)
        return dst

    files = {}
    original_name = ''
    for key in ('product', 'upper', 'lower'):
        f = request.files.get(key)
        if f and f.filename:
            files[key] = _save(f)
            if not original_name:
                original_name = f.filename
        else:
            p = (form.get(key + '_path') or '').strip()
            # ★只接受本服务上传目录内的路径★：否则调用方可指定任意服务端
            #   文件路径，让服务器读取/解析磁盘上任何文件。
            if p and os.path.exists(p) and (
                    _path_within(p, UPLOAD_DIR) or _path_within(p, _MK_UPLOADS)):
                files[key] = p
                if not original_name:
                    original_name = os.path.basename(p)
    for k in list(request.files.keys()):
        if k in extra_keys:
            f = request.files[k]
            if f and f.filename:
                params[k] = _save(f)
    try:
        from moldkit.pipeline import clean_part_name as _clean2
    except Exception:
        _clean2 = lambda x: x
    part = (form.get('part_name') or '').strip()
    part = _clean2(part) if part else _clean2(original_name or 'part')
    job = _MkJob(jid, methods, params, files, part)
    _remember(_MK_JOBS, jid, job)
    threading.Thread(target=_mk_worker, args=(job,), daemon=True).start()
    return jsonify({'job': jid, 'part_name': part})


@app.route('/api/moldkit/job/<jid>')
def api_mk_job(jid):
    job = _MK_JOBS.get(jid)
    if not job:
        return jsonify({'error': '没有这个任务'}), 404
    return jsonify(job.snap())


@app.route('/api/moldkit/file/<jid>/<path:fname>')
def api_mk_file(jid, fname):
    job = _MK_JOBS.get(jid)
    if not job:
        return jsonify({'error': '没有这个任务'}), 404
    p = os.path.join(job.workdir, os.path.basename(fname))
    if not os.path.exists(p):
        return jsonify({'error': '没有这个文件'}), 404
    return send_file(p, as_attachment=('download' in request.args))


if __name__ == '__main__':
    # 端口：默认 5001（5000 已被其它系统占用）。
    # 如需同时运行多个系统，用 --port 指定不同端口，无需改代码：
    #   python app.py --port 5001   → http://127.0.0.1:5001
    #   python app.py --port 5002   → http://127.0.0.1:5002
    #   python app.py --port 5003   → http://127.0.0.1:5003
    _port = 5001
    if '--port' in sys.argv:
        try:
            _port = int(sys.argv[sys.argv.index('--port') + 1])
        except (ValueError, IndexError):
            print("[App] --port 参数无效，使用默认端口 5001")
    # ★安全默认值：只监听回环★。本服务无任何认证，暴露到局域网等于把
    # 文件上传/下载/任意路径读取能力开放给同网段所有主机。
    # 确需局域网访问时显式加 --host 0.0.0.0，并自行补认证。
    _host = '127.0.0.1'
    if '--host' in sys.argv:
        try:
            _host = sys.argv[sys.argv.index('--host') + 1]
        except IndexError:
            print("[App] --host 参数无效，使用默认 127.0.0.1")
    # debug 关闭避免双进程加载；threaded=True：模具修复(约30-60秒)期间
    # 不阻塞其它请求(下载/查看等)。
    if _host != '127.0.0.1':
        print("[App] ⚠ 非回环绑定且无认证，同网段主机均可调用本服务全部接口")
    print(f"[App] 启动地址: http://{_host}:{_port}")
    app.run(host=_host, port=_port, debug=False, threaded=True)
