# -*- coding: utf-8 -*-
"""法二流程诊断：对三个产品跑 法二B(产品实体)，并复现 法二A(形变面) 缺陷。

输出每件的 solids/shells/valid/bbox/volume，以及
  · 上模 bbox 顶面是否 == Z_A + h1
  · 下模 bbox 顶面是否 == Z_A
  · Common(上模,产品) / Common(下模,产品) 干涉体积
"""
import os
import sys
import json
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer

from mold_fix import MoldFixer
from mold_method2_generator import MoldMethod2Generator

PRODUCTS = [
    ('2ROM31-6061', os.path.join(ROOT, 'web_box_generator', 'uploads',
                                 '543e0fdcfa2e_2ROM31-6061 ---(1).stp')),
    ('YA-1131', os.path.join(ROOT, 'YA-1131-505_MIRROR_xOy.stp')),
    ('YA-1238', os.path.join(ROOT, 'YA-1238-01_MIRROR_xOy.stp')),
]

CONTACT = {
    # 已存在的形变面 STEP（法二A 用）
    '2ROM31-6061': (
        os.path.join(ROOT, 'web_box_generator', 'uploads',
                     '925e79889839_2ROM31-6061 ---(1)_PUNCH_CONTACT_FACES.stp'),
        os.path.join(ROOT, 'web_box_generator', 'uploads',
                     'b852a7a025e8_2ROM31-6061 ---(1)_CAVITY_CONTACT_FACES.stp')),
    'YA-1131': (
        os.path.join(ROOT, 'web_box_generator', 'uploads',
                     '204cfaba3f3f_YA-1131-505_MIRROR_xOy_PUNCH_CONTACT_FACES.stp'),
        os.path.join(ROOT, 'web_box_generator', 'uploads',
                     'b721182a27e0_YA-1131-505_MIRROR_xOy_CAVITY_CONTACT_FACES.stp')),
    'YA-1238': (
        os.path.join(ROOT, 'web_box_generator', 'uploads',
                     '656d07b0bbcb_YA-1238-01_MIRROR_xOy_PUNCH_CONTACT_FACES.stp'),
        os.path.join(ROOT, 'web_box_generator', 'uploads',
                     'd007a86fd7cc_YA-1238-01_MIRROR_xOy_CAVITY_CONTACT_FACES.stp')),
}

OUT = os.path.join(ROOT, '_m2_debug', 'out')


def n_of(shape, kind):
    n = 0
    e = TopExp_Explorer(shape, kind)
    while e.More():
        n += 1
        e.Next()
    return n


def common_vol(a, b, fuzz=1e-3):
    try:
        c = BRepAlgoAPI_Common(a, b)
        c.SetFuzzyValue(fuzz)
        c.Build()
        if c.IsDone() and not c.Shape().IsNull():
            return MoldFixer._solid_volume(c.Shape())
    except Exception as e:
        return ('ERR', str(e))
    return None


def report(tag, shape):
    bb = MoldFixer._bbox_tuple(shape)
    print(f"    {tag}: solids={n_of(shape, TopAbs_SOLID)} shells={n_of(shape, TopAbs_SHELL)} "
          f"valid={BRepCheck_Analyzer(shape).IsValid()} "
          f"vol={MoldFixer._solid_volume(shape):.1f}")
    print(f"    {tag}: bbox=[{bb[0]:.3f},{bb[1]:.3f},{bb[2]:.3f} .. "
          f"{bb[3]:.3f},{bb[4]:.3f},{bb[5]:.3f}]")
    return bb


def run_product_B(name, path):
    print(f"\n{'=' * 78}\n[法二B] {name}\n  {path}")
    t0 = time.time()
    solids = MoldFixer._extract_solids(path)
    product = MoldFixer._make_compound([s for _, s in solids])
    pb = MoldFixer._bbox_tuple(product)
    Z_A, Z_B = pb[5], pb[2]
    dt = Z_A - Z_B
    print(f"    产品: {len(solids)} 实体, bbox z[{Z_B:.3f},{Z_A:.3f}] 高={dt:.3f}, "
          f"xy=[{pb[0]:.2f},{pb[1]:.2f} .. {pb[3]:.2f},{pb[4]:.2f}]")

    g = MoldMethod2Generator()
    r = g.generate_from_product(path)
    if not r.get('success'):
        print(f"    !! 法二B 失败: {r.get('error')}")
        return {'name': name, 'success': False, 'error': r.get('error')}
    out = os.path.join(OUT, name)
    os.makedirs(out, exist_ok=True)
    rec = {'name': name, 'success': True, 'Z_A': Z_A, 'Z_B': Z_B, 'dt': dt}
    for kind in ('punch', 'cavity'):
        res = r[kind]
        d = res['details']
        h1 = d.get('h1', dt)
        shp = res['_shape']
        print(f"  -- {kind} (details={ {k: v for k, v in d.items() if k != 'valid'} })")
        bb = report(kind, shp)
        exp_top = (Z_A + h1) if kind == 'punch' else Z_A
        top_ok = abs(bb[5] - exp_top) < 0.05
        iv = common_vol(shp, product)
        print(f"    {kind}: Common({kind},产品)={iv}   顶面期望={exp_top:.3f} "
              f"实际={bb[5]:.3f} top_ok={top_ok}")
        g.export_step(res, os.path.join(out, f'{kind}.stp'))
        rec[kind] = {'bbox': bb, 'vol': res['volume'], 'details': d,
                     'top_ok': top_ok, 'interfere': iv,
                     'shells': n_of(shp, TopAbs_SHELL),
                     'solids': n_of(shp, TopAbs_SOLID)}
    rec['secs'] = round(time.time() - t0, 1)
    print(f"  用时 {rec['secs']}s")
    return rec


def run_product_A(name, upper, lower):
    print(f"\n{'-' * 78}\n[法二A] {name}  (形变面拉伸)")
    for p, tag in ((upper, 'PUNCH_CONTACT'), (lower, 'CAVITY_CONTACT')):
        if not os.path.exists(p):
            print(f"    跳过 {tag}：文件不存在 {p}")
            return
    g = MoldMethod2Generator()
    out = {}
    for fn, meth, tag in ((g.generate_punch, upper, 'punch'),
                          (g.generate_cavity, lower, 'cavity')):
        try:
            res = fn(upper if tag == 'punch' else lower)
        except Exception:
            traceback.print_exc()
            continue
        if not res.get('success'):
            print(f"    {tag} 失败: {res.get('error')}")
            continue
        d = res['details']
        bb = report(f'A-{tag}', res['_shape'])
        if tag == 'punch':
            exp = d['P']
        else:
            exp = d['z_top']
        print(f"    A-{tag}: 期望顶面={exp:.3f} 实际={bb[5]:.3f} "
              f"超出={bb[5] - exp:+.3f}  {'<== 缺陷' if abs(bb[5] - exp) > 0.05 else 'OK'}")
        out[tag] = {'bbox': bb, 'vol': res['volume'], 'details': d,
                    'over': bb[5] - exp}
    return out


def main():
    allrec = []
    for name, path in PRODUCTS:
        if not os.path.exists(path):
            print(f"!! 产品不存在: {path}")
            continue
        try:
            rec = run_product_B(name, path)
        except Exception:
            traceback.print_exc()
            rec = {'name': name, 'success': False, 'error': 'exception'}
        allrec.append(rec)
        try:
            up, lo = CONTACT[name]
            rec['methodA'] = run_product_A(name, up, lo)
        except Exception:
            traceback.print_exc()

    with open(os.path.join(ROOT, '_m2_debug', 'diag_result.json'), 'w',
              encoding='utf-8') as f:
        json.dump(allrec, f, ensure_ascii=False, indent=2, default=str)
    print("\n完成，结果写入 _m2_debug/diag_result.json")


if __name__ == '__main__':
    main()
