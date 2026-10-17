# -*- coding: utf-8 -*-
"""moldkit Web 控制台 —— 「自动生成模具」软件的界面。

设计：
    · 后台线程跑任务，前端轮询日志（任务动辄几十秒，同步会卡死浏览器）
    · 方法卡片式选择，参数表单由方法自己声明（registry 提供）
    · 结果区：指标表 + 干涉/贴合度 + 产物下载 + 轮廓预览图
    · 一键"全部方法对比"

启动：
    python -m moldkit.webapp            # 默认 5002
    python -m moldkit.webapp 5003
"""
import json
import os
import sys
import threading
import time
import traceback
import uuid

from flask import Flask, jsonify, request, send_file, render_template_string

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from moldkit import pipeline, registry  # noqa: E402

WORKROOT = os.path.join(ROOT, "mold_jobs")
UPLOADS = os.path.join(WORKROOT, "_uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 400 * 1024 * 1024

JOBS = {}
_LOCK = threading.Lock()
_JOBS_MAX = 64          # 任务元数据上限，超过淘汰最旧（防字典无限增长）
_JOBDIR_TTL_S = 24 * 3600   # 工作目录保留时长，过期在启动时清理


def _path_within(child, parent):
    """判断 child 是否位于 parent 目录内（commonpath 按路径段比较，
    杜绝 '/x/uploads_evil' 绕过 '/x/uploads' 的前缀匹配缺陷）。"""
    try:
        p = os.path.abspath(parent)
        return os.path.commonpath([os.path.abspath(child), p]) == p
    except ValueError:
        return False


def _remember_job(job):
    """登记任务并限量留存。"""
    JOBS[job.id] = job
    while len(JOBS) > _JOBS_MAX:
        JOBS.pop(next(iter(JOBS)))


def _cleanup_stale_workdirs():
    """清理超过 TTL 的旧任务工作目录（产物已下载完毕的不会再用）。"""
    now = time.time()
    try:
        for name in os.listdir(WORKROOT):
            d = os.path.join(WORKROOT, name)
            if not os.path.isdir(d) or name == "_uploads":
                continue
            try:
                if now - os.path.getmtime(d) > _JOBDIR_TTL_S:
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
class WebJob:
    def __init__(self, jid, methods, params, files, part_name):
        self.id = jid
        self.methods = methods
        self.params = params
        self.files = files
        self.part_name = part_name
        self.log = []
        self.status = "running"     # running | done | error
        self.results = {}
        self.error = None
        self.t0 = time.time()
        self.workdir = os.path.join(WORKROOT, jid)
        os.makedirs(self.workdir, exist_ok=True)

    def say(self, s=""):
        with _LOCK:
            self.log.append(str(s))
            if len(self.log) > 4000:
                del self.log[:1000]

    def snapshot(self):
        with _LOCK:
            log = list(self.log)
        arts = []
        for f in sorted(os.listdir(self.workdir)):
            p = os.path.join(self.workdir, f)
            if os.path.isfile(p):
                arts.append({"name": f, "size": os.path.getsize(p)})
        return {
            "id": self.id, "status": self.status, "log": log[-400:],
            "elapsed": round(time.time() - self.t0, 1),
            "results": self.results, "error": self.error, "artifacts": arts,
        }


def _run_job(job):
    try:
        # ★先自检/自愈 SolidWorks 引擎★（VBE 在这台机器上不落盘，SW 重启即丢）
        try:
            from moldkit.swjobs import ensure_engine
            ok, msg = ensure_engine(log=job.say)
            job.say("[engine] %s" % msg)
            if not ok:
                job.error = msg
                job.status = "error"
                return
        except Exception as e:
            job.say("[engine] 自检异常（继续尝试运行）: %r" % (e,))

        ctx = pipeline.make_context(job.workdir,
                                    product=job.files.get("product"),
                                    upper=job.files.get("upper"),
                                    lower=job.files.get("lower"),
                                    params=job.params, part_name=job.part_name,
                                    log=job.say)
        job.say("工作目录: %s" % job.workdir)
        job.say("零件名  : %s" % job.part_name)
        out = {}
        if len(job.methods) == 1:
            o = pipeline.run(job.methods[0], ctx, log=job.say)
            out[job.methods[0]] = o
        else:
            out = pipeline.run_all(ctx, only=job.methods, log=job.say)
        pipeline.summarize(out, log=job.say)
        rp = os.path.join(job.workdir, "%s_report.json" % job.part_name)
        pipeline.save_report(out, rp)
        job.results = {k: v.to_dict() for k, v in out.items()}
        job.status = "done"
    except Exception as e:
        job.error = "%s" % e
        job.say(traceback.format_exc())
        job.status = "error"


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@app.route("/api/methods")
def api_methods():
    out = []
    for info in registry.list_info():
        try:
            m = registry.get(info["name"])
            ok, why = m.available()
            info["available"] = ok
            info["unavailable_reason"] = why
        except Exception as e:
            info["available"] = False
            info["unavailable_reason"] = str(e)
        out.append(info)
    return jsonify({"methods": out})


@app.route("/api/recommend")
def api_recommend():
    files = _files_from_request({})
    try:
        ctx = pipeline.make_context(WORKROOT, product=files.get("product"),
                                    upper=files.get("upper"), lower=files.get("lower"),
                                    log=lambda s="": None)
        best, cands, note = registry.recommend(ctx)
        return jsonify({"best": best.name if best else None,
                        "candidates": [m.name for m in cands], "note": note})
    except Exception as e:
        return jsonify({"best": None, "candidates": [], "note": str(e)})


def _files_from_request(form):
    out = {}
    for key in ("product", "upper", "lower"):
        # 1) 服务器上已有路径（★只接受上传目录内的路径★：
        #    否则调用方可指定任意服务端文件，让服务器读取/解析磁盘上任何文件）
        p = (form.get(key + "_path") or "").strip()
        if p and os.path.exists(p) and _path_within(p, UPLOADS):
            out[key] = p
            continue
        # 2) 本次上传
        f = request.files.get(key) if request.files else None
        if f and f.filename:
            # 文件名净化：客户端可控，防 '../' 路径穿越
            safe = os.path.basename(f.filename.replace("\\", "/"))
            safe = "".join(c if (c.isalnum() or c in "._-\u4e00-\u9fff") else "_"
                           for c in safe).strip("._") or "input.stp"
            dst = os.path.join(UPLOADS, "%s_%s" % (uuid.uuid4().hex[:8], safe))
            if not _path_within(dst, UPLOADS):
                continue
            f.save(dst)
            out[key] = dst
    return out


@app.route("/api/run", methods=["POST"])
def api_run():
    form = request.form
    methods = form.getlist("methods") or []
    if not methods:
        return jsonify({"error": "没有选择方法"}), 400
    params = {}
    for k, v in form.items():
        if k.startswith("p."):
            params[k[2:]] = v
    # ★额外输入文件★（例如方法一的凸/凹模毛坯）：存盘后写进同名参数
    extra_keys = set()
    for info in registry.list_info():
        for x in info.get("extra_inputs", []):
            extra_keys.add(x["key"])
    for k in list(request.files.keys()):
        if k in extra_keys:
            f = request.files[k]
            if f and f.filename:
                dst = os.path.join(UPLOADS, "%s_%s" % (uuid.uuid4().hex[:8],
                                                       os.path.basename(f.filename)))
                f.save(dst)
                params[k] = dst
    files = _files_from_request(form)
    part = (form.get("part_name") or "").strip()
    jid = uuid.uuid4().hex[:10]
    job = WebJob(jid, methods, params, files, part or "part")
    _remember_job(job)
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return jsonify({"job": jid})


@app.route("/api/job/<jid>")
def api_job(jid):
    job = JOBS.get(jid)
    if not job:
        return jsonify({"error": "没有这个任务"}), 404
    return jsonify(job.snapshot())


@app.route("/api/file/<jid>/<path:fname>")
def api_file(jid, fname):
    job = JOBS.get(jid)
    if not job:
        return jsonify({"error": "没有这个任务"}), 404
    p = os.path.join(job.workdir, os.path.basename(fname))
    if not os.path.exists(p):
        return jsonify({"error": "没有这个文件"}), 404
    return send_file(p, as_attachment="download" in request.args)


@app.route("/")
def index():
    return render_template_string(PAGE)


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>模具自动生成</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--line:#2a2f3a;--fg:#e6e9ef;--dim:#98a1b3;
      --acc:#4c8dff;--ok:#3ddc84;--bad:#ff5c5c;--warn:#ffb020}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);
       display:flex;align-items:baseline;gap:14px}
h1{font-size:19px;margin:0}
.sub{color:var(--dim);font-size:12.5px}
main{display:grid;grid-template-columns:minmax(360px,420px) 1fr;gap:0;height:calc(100vh - 62px)}
.left{padding:18px 20px;overflow:auto;border-right:1px solid var(--line)}
.right{padding:18px 20px;overflow:auto}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
   margin:22px 0 8px;font-weight:600}
h2:first-child{margin-top:0}
label{display:block;margin:10px 0 4px;font-size:12.5px;color:var(--dim)}
input[type=text],input[type=file],select{width:100%;padding:7px 9px;background:#12151b;
   border:1px solid var(--line);border-radius:6px;color:var(--fg);font-size:13px}
input[type=text]:focus,select:focus{outline:none;border-color:var(--acc)}
.mcard{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;
       cursor:pointer;background:var(--card);transition:.12s}
.mcard:hover{border-color:#3d465a}
.mcard.on{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}
.mcard.off{opacity:.45;cursor:not-allowed}
.mhead{display:flex;align-items:center;gap:8px}
.mname{font-weight:600;font-size:13.5px}
.tag{font-size:10.5px;padding:1px 6px;border-radius:99px;background:#222834;color:var(--dim)}
.tag.sw{background:#1d3448;color:#7cc0ff}
.tag.ocp{background:#2f2a1d;color:#ffce6b}
.mdesc{color:var(--dim);font-size:12px;margin-top:4px}
.mlim{color:#c9a04a;font-size:11.5px;margin-top:3px}
button{background:var(--acc);color:#fff;border:0;border-radius:7px;padding:10px 16px;
       font-size:14px;font-weight:600;cursor:pointer;width:100%;margin-top:14px}
button.sec{background:#252b36;color:var(--fg);font-weight:500}
button:disabled{opacity:.5;cursor:not-allowed}
pre{background:#0b0d11;border:1px solid var(--line);border-radius:8px;padding:12px;
    overflow:auto;font-size:12px;line-height:1.5;max-height:44vh;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:8px}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left}
th{color:var(--dim);font-weight:600}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.arts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.arts a{background:#1a1f28;border:1px solid var(--line);border-radius:6px;
        padding:5px 9px;color:var(--fg);text-decoration:none;font-size:12px}
.arts a:hover{border-color:var(--acc)}
.param{border-left:2px solid var(--line);padding-left:10px;margin:8px 0}
.hint{font-size:11.5px;color:var(--dim);margin-top:2px}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%;background:var(--acc);width:30%;
       animation:mv 1.1s ease-in-out infinite}
@keyframes mv{0%{margin-left:-30%}100%{margin-left:100%}}
img.prev{max-width:100%;border:1px solid var(--line);border-radius:8px;margin-top:8px}
</style></head><body>
<header>
  <h1>模具自动生成</h1>
  <span class="sub">统一方法选择 · SolidWorks + OpenCASCADE 双引擎 · 自动校验</span>
</header>
<main>
 <div class="left">
  <h2>1. 输入</h2>
  <label>零件名（输出文件前缀）</label>
  <input type="text" id="part" placeholder="例如 2ROM31-6061">
  <label>产品实体 STEP <span class="hint">（方法二B / 四 需要）</span></label>
  <input type="file" id="f_product" accept=".stp,.step">
  <label>上形变面 STEP <span class="hint">（方法二A / 三 需要）</span></label>
  <input type="file" id="f_upper" accept=".stp,.step">
  <label>下形变面 STEP</label>
  <input type="file" id="f_lower" accept=".stp,.step">

  <h2>2. 选方法（可多选做对比）</h2>
  <div id="methods"></div>
  <button class="sec" onclick="recommend()">按输入自动推荐</button>
  <div id="recnote" class="hint"></div>

  <h2>3. 参数</h2>
  <div id="params" class="hint">选择方法后这里显示可调参数</div>

  <button id="runbtn" onclick="run()">开始生成</button>
  <button class="sec" onclick="runAll()">所有可用方法都跑一遍对比</button>
 </div>

 <div class="right">
  <h2>运行日志 / 结果</h2>
  <div id="status" class="hint">等待开始</div>
  <div class="bar" id="bar" style="display:none"><i></i></div>
  <div id="summary"></div>
  <div id="arts" class="arts"></div>
  <div id="preview"></div>
  <pre id="log">—</pre>
 </div>
</main>
<script>
let METHODS=[], chosen=new Set(), curJob=null, timer=null;
// ★HTML 转义★：方法名/错误信息/产物文件名都可能来自用户上传的文件名，
// 直接 innerHTML 拼接会形成存储型 XSS，统一走 esc()。
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function loadMethods(){
  const r=await (await fetch('/api/methods')).json();
  METHODS=r.methods;
  const box=document.getElementById('methods');
  box.innerHTML=METHODS.map(m=>`
    <div class="mcard ${m.available?'':'off'}" data-n="${esc(m.name)}" onclick="pick('${esc(m.name)}')">
      <div class="mhead">
        <span class="mname">${esc(m.label)}</span>
        <span class="tag ${m.engine=='solidworks'?'sw':'ocp'}">${esc(m.engine)}</span>
      </div>
      <div class="mdesc">${esc(m.desc||'')}</div>
      ${m.cons?`<div class="mlim">限制：${esc(m.cons)}</div>`:''}
      ${m.available?'':`<div class="mlim">不可用：${esc(m.unavailable_reason||'')}</div>`}
    </div>`).join('');
  renderParams();
}
function pick(n){
  const m=METHODS.find(x=>x.name==n);
  if(!m||!m.available) return;
  if(chosen.has(n)) chosen.delete(n); else chosen.add(n);
  document.querySelectorAll('.mcard').forEach(e=>{
    const on=chosen.has(e.dataset.n);
    e.classList.toggle('on',on);
  });
  renderParams();
}
function renderParams(){
  const box=document.getElementById('params');
  const names=[...chosen];
  if(!names.length){box.innerHTML='选择方法后这里显示可调参数';return;}
  let html='';
  for(const n of names){
    const m=METHODS.find(x=>x.name==n);
    if(!m) continue;
    const xtra=(m.extra_inputs||[]);
    if((!m.params||!m.params.length)&&!xtra.length) continue;
    html+=`<div class="param"><b>${esc(m.label)}</b>`;
    for(const x of xtra){
      html+=`<label>${esc(x.label)}</label>
             <input type="file" id="x.${esc(x.key)}" accept="${esc(x.accept||'.stp,.step')}">`;
    }
    for(const p of m.params){
      if(xtra.some(x=>x.key===p.name)) continue;   // 已用文件输入口代替
      const id=`p.${p.name}`;
      html+=`<label>${esc(p.label)}${p.unit?' ('+esc(p.unit)+')':''}</label>
             <input type="text" id="${id}" value="${esc(p.default)}">
             ${p.help?`<div class="hint">${esc(p.help)}</div>`:''}`;
    }
    html+='</div>';
  }
  box.innerHTML=html||'<span class="hint">这些方法没有可调参数</span>';
}
async function recommend(){
  const fd=new FormData();
  ['product','upper','lower'].forEach(k=>{
    const f=document.getElementById('f_'+k).files[0];
    if(f) fd.append(k,f);
  });
  const r=await (await fetch('/api/recommend',{method:'POST',body:fd})).json();
  document.getElementById('recnote').innerHTML=
    (r.best?`推荐 <b>${esc(r.best)}</b>。`:'')+esc(r.note||'');
  chosen=new Set(r.candidates||[]);
  document.querySelectorAll('.mcard').forEach(e=>e.classList.toggle('on',chosen.has(e.dataset.n)));
  renderParams();
}
function buildForm(methods){
  const fd=new FormData();
  methods.forEach(m=>fd.append('methods',m));
  fd.append('part_name',document.getElementById('part').value||'');
  ['product','upper','lower'].forEach(k=>{
    const f=document.getElementById('f_'+k).files[0];
    if(f) fd.append(k,f);
  });
  document.querySelectorAll('input[id^="p."]').forEach(e=>fd.append(e.id,e.value));
  // ★额外输入文件（例如方法一的凸/凹模毛坯）★
  document.querySelectorAll('input[id^="x."]').forEach(e=>{
    if(e.files&&e.files[0]) fd.append(e.id.slice(2),e.files[0]);
  });
  return fd;
}
async function start(methods){
  if(!methods.length){alert('先选方法');return;}
  const fd=buildForm(methods);
  const r=await fetch('/api/run',{method:'POST',body:fd});
  const j=await r.json();
  if(j.error){alert(j.error);return;}
  curJob=j.job;
  document.getElementById('status').textContent='运行中…';
  document.getElementById('bar').style.display='block';
  document.getElementById('summary').innerHTML='';
  document.getElementById('preview').innerHTML='';
  if(timer) clearInterval(timer);
  timer=setInterval(poll,900);
}
function run(){start([...chosen]);}
function runAll(){start(METHODS.filter(m=>m.available).map(m=>m.name));}
async function poll(){
  if(!curJob) return;
  const s=await (await fetch('/api/job/'+curJob)).json();
  document.getElementById('log').textContent=(s.log||[]).join('\n');
  document.getElementById('log').scrollTop=1e9;
  document.getElementById('status').textContent=
    (s.status=='running'?'运行中…':s.status=='done'?'完成':'失败')+`  ${s.elapsed}s`;
  if(s.status!='running'){
    clearInterval(timer);timer=null;
    document.getElementById('bar').style.display='none';
    render(s);
  }
}
function render(s){
  let h='<table><tr><th>方法</th><th>结果</th><th>上模体积</th><th>下模体积</th><th>干涉</th><th>贴合度</th></tr>';
  for(const [n,o] of Object.entries(s.results||{})){
    const rep=(o.metrics&&o.metrics.report)||{};
    const pv=rep.punch?Math.round(rep.punch.volume_mm3).toLocaleString():'-';
    const cv=rep.cavity?Math.round(rep.cavity.volume_mm3).toLocaleString():'-';
    const iv=(rep.pair&&rep.pair.interference_mm3!=null)?rep.pair.interference_mm3.toFixed(3):'-';
    const co=(rep.punch_conformity&&rep.punch_conformity.ratio!=null)?
        (rep.punch_conformity.ratio*100).toFixed(1)+'%':'-';
    h+=`<tr><td>${esc(n)}</td>
        <td class="${o.ok?'ok':'bad'}">${o.ok?'成功':'失败'}</td>
        <td>${pv}</td><td>${cv}</td><td>${iv}</td><td>${co}</td></tr>`;
    if(!o.ok) h+=`<tr><td colspan="6" class="bad">${esc(o.error||'')}</td></tr>`;
  }
  h+='</table>';
  document.getElementById('summary').innerHTML=h;
  let a='';
  for(const f of (s.artifacts||[])){
    if(f.name.endsWith('.stp'))
      a+=`<a href="/api/file/${s.id}/${encodeURIComponent(f.name)}?download=1">${esc(f.name)} (${Math.round(f.size/1024)}KB)</a>`;
  }
  document.getElementById('arts').innerHTML=a;
  let p='';
  for(const f of (s.artifacts||[])){
    if(f.name.endsWith('_outline.png'))
      p+=`<div class="hint">${esc(f.name)}</div><img class="prev" src="/api/file/${s.id}/${encodeURIComponent(f.name)}">`;
  }
  document.getElementById('preview').innerHTML=p;
}
loadMethods();
</script></body></html>
"""


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5002
    _cleanup_stale_workdirs()
    print("模具自动生成控制台  ->  http://127.0.0.1:%d" % port)
    print("工作目录:", WORKROOT)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
