from __future__ import annotations

import cgi
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(os.environ.get("K_DESK_ROOT", Path(__file__).resolve().parents[2]))
LEGACY_RISK_ROOT = Path(r"D:\risk")
DEFAULT_OUT_DIR = LEGACY_RISK_ROOT / "output_data" if LEGACY_RISK_ROOT.exists() else ROOT / "outputs" / "kline"
DEFAULT_PYDEPS = LEGACY_RISK_ROOT / "pydeps" if (LEGACY_RISK_ROOT / "pydeps").exists() else ROOT / "pydeps"
OUT_DIR = Path(os.environ.get("TRADE_KLINE_OUT_DIR", DEFAULT_OUT_DIR))
UPLOAD_DIR = OUT_DIR / "uploaded_statements"
TOOL_DIR = Path(os.environ.get("TRADE_KLINE_TOOL_DIR", ROOT / "tools" / "trade_kline_tool"))
GENERATOR = TOOL_DIR / "generate_trade_kline_from_statement.py"
PYTHON = Path(os.environ.get("K_DESK_PYTHON", r"C:\Users\amber\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"))
TERMINAL = Path(r"C:\Program Files\AC Capital Market MT5 Terminal\terminal64.exe")
HOST = "127.0.0.1"
PORT = int(os.environ.get("TRADE_KLINE_WEB_PORT", "8765"))

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_filename(name: str) -> str:
    name = Path(name or "statement.html").name
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return name or "statement.html"


def file_url(path: Path) -> str:
    return "file:///" + str(path.resolve()).replace("\\", "/")


def public_output_url(path: Path) -> str:
    return "/output/" + quote(path.name)


def json_response(handler: BaseHTTPRequestHandler, data: dict, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def update_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        job = JOBS.setdefault(job_id, {})
        job.update(updates)
        job["updated_at"] = now_text()


def append_log(job_id: str, text: str) -> None:
    if not text:
        return
    with JOBS_LOCK:
        job = JOBS.setdefault(job_id, {})
        logs = job.setdefault("logs", "")
        job["logs"] = (logs + text)[-80000:]
        job["updated_at"] = now_text()


def parse_preview_json(output: str) -> dict:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("inspect did not return JSON")
    return json.loads(output[start : end + 1])


def run_inspection(job_id: str, statement_path: Path) -> None:
    update_job(job_id, status="parsed_running", started_at=now_text(), message="正在解析报表产品和时间范围")
    cmd = [str(PYTHON), str(GENERATOR), str(statement_path), "--inspect", "--out-dir", str(OUT_DIR)]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TRADE_KLINE_PYDEPS", str(DEFAULT_PYDEPS))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
        output = proc.stdout or ""
        append_log(job_id, output)
        if proc.returncode != 0:
            update_job(job_id, status="failed", message="解析失败，查看日志", finished_at=now_text(), return_code=proc.returncode)
            return
        preview = parse_preview_json(output)
        update_job(
            job_id,
            status="parsed",
            message="已解析，请选择要生成的产品和时间段",
            preview=preview,
            return_code=proc.returncode,
        )
    except Exception as exc:
        append_log(job_id, f"\nERROR: {exc}\n")
        update_job(job_id, status="failed", message=str(exc), finished_at=now_text())


def generated_files_for(html_path: Path | None) -> list[dict]:
    if not html_path:
        return []
    name = html_path.name
    if not name.endswith("_trade_kline.html"):
        return []
    stem = name[: -len("_trade_kline.html")]
    files = []
    for path in sorted(OUT_DIR.glob(stem + "*")):
        if path.is_file():
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "url": public_output_url(path),
                    "file_url": file_url(path),
                }
            )
    return files


def parse_html_path(output: str) -> Path | None:
    matches = re.findall(r"[A-Za-z]:\\[^\r\n]+?_trade_kline\.html", output)
    for match in reversed(matches):
        path = Path(match.strip())
        if path.exists():
            return path
    return None


def run_generation(job_id: str, statement_path: Path, symbols: list[str] | None = None, start: str = "", end: str = "") -> None:
    update_job(job_id, status="running", started_at=now_text(), message="正在解析并生成图表")
    cmd = [
        str(PYTHON),
        str(GENERATOR),
        str(statement_path),
        "--out-dir",
        str(OUT_DIR),
        "--terminal",
        str(TERMINAL),
    ]
    if symbols:
        cmd.extend(["--symbols", ",".join(symbols)])
    if start:
        cmd.extend(["--start", start])
    if end:
        cmd.extend(["--end", end])
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TRADE_KLINE_PYDEPS", str(DEFAULT_PYDEPS))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        output_parts: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            output_parts.append(line)
            append_log(job_id, line)
        code = proc.wait()
        output = "".join(output_parts)
        html_path = parse_html_path(output)
        if code == 0 and html_path:
            files = generated_files_for(html_path)
            update_job(
                job_id,
                status="done",
                message="生成完成",
                finished_at=now_text(),
                html_path=str(html_path),
                html_url=public_output_url(html_path),
                html_file_url=file_url(html_path),
                files=files,
                return_code=code,
            )
        else:
            update_job(
                job_id,
                status="failed",
                message="生成失败，查看日志",
                finished_at=now_text(),
                return_code=code,
                html_path=str(html_path) if html_path else "",
            )
    except Exception as exc:
        append_log(job_id, f"\nERROR: {exc}\n")
        update_job(job_id, status="failed", message=str(exc), finished_at=now_text())


def recent_charts(limit: int = 20) -> list[dict]:
    rows = []
    for path in OUT_DIR.glob("*_trade_kline.html"):
        if not path.is_file():
            continue
        rows.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "url": public_output_url(path),
                "file_url": file_url(path),
            }
        )
    rows.sort(key=lambda item: item["mtime"], reverse=True)
    return rows[:limit]


def recover_job(job_id: str) -> dict:
    uploads = sorted(UPLOAD_DIR.glob(f"{job_id}_*"))
    if not uploads:
        return {"id": job_id, "status": "missing", "message": "任务不存在"}
    statement = uploads[0]
    account_match = re.search(r"(?:Statement|ReportHistory)[_ -]?(\d+)", statement.name, re.I)
    candidates = []
    if account_match:
        account = account_match.group(1)
        candidates = sorted(
            OUT_DIR.glob(f"{account}_*_trade_kline.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if not candidates:
        upload_time = statement.stat().st_mtime
        candidates = sorted(
            [p for p in OUT_DIR.glob("*_trade_kline.html") if p.stat().st_mtime >= upload_time],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if candidates:
        html_path = candidates[0]
        return {
            "id": job_id,
            "status": "done",
            "message": "生成完成（服务重启后自动恢复）",
            "statement": str(statement),
            "html_path": str(html_path),
            "html_url": public_output_url(html_path),
            "html_file_url": file_url(html_path),
            "files": generated_files_for(html_path),
            "logs": "服务重启后从输出文件自动恢复任务状态；详细实时日志不再保留。\n",
        }
    return {
        "id": job_id,
        "status": "failed",
        "message": "任务状态已丢失，且未找到对应输出图表",
        "statement": str(statement),
        "logs": "服务重启后未找到对应输出图表，请重新上传生成。\n",
    }


BASE_CSS = """
body{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,"Microsoft YaHei",sans-serif}
header{background:#111827;color:white;padding:16px 22px}
h1{margin:0;font-size:20px}
.meta{margin-top:6px;color:#cbd5e1;font-size:13px}
main{padding:18px 22px 28px}
.panel{background:white;border:1px solid #d9e2ec;border-radius:6px;padding:16px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=file]{border:1px solid #cbd5e1;padding:9px;background:#fff;border-radius:4px;min-width:360px}
button,.btn{border:1px solid #111827;background:#111827;color:white;border-radius:4px;padding:9px 13px;cursor:pointer;text-decoration:none;display:inline-block}
.btn.secondary{background:#fff;color:#111827;border-color:#cbd5e1}
.hint{color:#64748b;font-size:13px;margin-top:8px;line-height:1.5}
.status{font-weight:700}
.done{color:#16a34a}.failed{color:#dc2626}.running{color:#2563eb}.queued{color:#7c3aed}
pre{white-space:pre-wrap;background:#0f172a;color:#dbeafe;padding:12px;border-radius:6px;max-height:360px;overflow:auto;font-size:12px}
table{width:100%;border-collapse:collapse;background:white;font-size:13px}
th,td{border:1px solid #e5e7eb;padding:7px 9px;text-align:left;white-space:nowrap}
th{background:#eef2f7}
.chartFrame{width:100%;height:760px;border:1px solid #cbd5e1;background:white}
"""


def page_shell(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header>
<h1>买卖点 K 线图生成工具</h1>
<div class="meta">上传交易报表后自动解析、核对时间口径、读取/复用 MT5 M1 缓存并生成图表。只读读取行情，不修改 MT5/Manager 数据。</div>
</header>
<main>{content}</main>
</body>
</html>"""


def index_page() -> str:
    rows = recent_charts()
    recent = "".join(
        f"<tr><td><a href='{item['url']}' target='_blank'>{html.escape(item['name'])}</a></td>"
        f"<td>{html.escape(item['mtime'])}</td><td>{item['size']:,}</td>"
        f"<td><a href='{item['url']}' target='_blank'>网页打开</a> · <a href='{item['file_url']}' target='_blank'>file 打开</a></td></tr>"
        for item in rows
    )
    if not recent:
        recent = "<tr><td colspan='4'>暂无生成记录</td></tr>"
    return page_shell(
        "买卖点 K 线图生成工具",
        f"""
<section class="panel">
  <form action="/upload" method="post" enctype="multipart/form-data">
    <div class="row">
      <input type="file" name="statement" accept=".htm,.html" required>
      <button type="submit">上传并生成</button>
    </div>
    <div class="hint">支持 MT5 statement / ReportHistory 的 .htm 或 .html 文件。输出和缓存保存在项目目录 <code>{html.escape(str(OUT_DIR))}</code>。</div>
  </form>
</section>
<section class="panel">
  <h2 style="font-size:16px;margin:0 0 10px">最近生成</h2>
  <table><thead><tr><th>图表</th><th>生成时间</th><th>大小</th><th>操作</th></tr></thead><tbody>{recent}</tbody></table>
</section>
""",
    )


def job_page(job_id: str) -> str:
    return page_shell(
        "生成任务",
        f"""
<section class="panel">
  <div class="row">
    <a class="btn secondary" href="/">返回上传</a>
    <span>任务 ID: <code>{html.escape(job_id)}</code></span>
    <span class="status queued" id="status">读取中</span>
  </div>
  <div class="hint" id="message"></div>
  <div class="row" id="actions" style="margin-top:10px"></div>
</section>
<section class="panel" id="previewPanel" style="display:none">
  <iframe class="chartFrame" id="chartFrame"></iframe>
</section>
<section class="panel">
  <h2 style="font-size:16px;margin:0 0 10px">生成日志</h2>
  <pre id="logs"></pre>
</section>
<section class="panel" id="filesPanel" style="display:none">
  <h2 style="font-size:16px;margin:0 0 10px">输出文件</h2>
  <table><thead><tr><th>文件</th><th>时间</th><th>大小</th><th>链接</th></tr></thead><tbody id="files"></tbody></table>
</section>
<script>
const jobId = {json.dumps(job_id)};
async function refresh(){{
  const res = await fetch('/api/jobs/' + jobId, {{cache:'no-store'}});
  const job = await res.json();
  const status = document.getElementById('status');
  status.textContent = job.status || 'unknown';
  status.className = 'status ' + (job.status || '');
  document.getElementById('message').textContent = job.message || '';
  document.getElementById('logs').textContent = job.logs || '';
  if (job.html_url) {{
    document.getElementById('actions').innerHTML =
      `<a class="btn" target="_blank" href="${{job.html_url}}">打开图表</a>` +
      `<a class="btn secondary" target="_blank" href="${{job.html_file_url}}">file 打开</a>`;
    document.getElementById('previewPanel').style.display = 'block';
    const frame = document.getElementById('chartFrame');
    if (!frame.src) frame.src = job.html_url;
  }}
  if (job.files && job.files.length) {{
    document.getElementById('filesPanel').style.display = 'block';
    document.getElementById('files').innerHTML = job.files.map(f =>
      `<tr><td>${{f.name}}</td><td>${{f.mtime}}</td><td>${{f.size.toLocaleString()}}</td>` +
      `<td><a target="_blank" href="${{f.url}}">网页</a> · <a target="_blank" href="${{f.file_url}}">file</a></td></tr>`
    ).join('');
  }}
  if (!['done','failed'].includes(job.status)) setTimeout(refresh, 1200);
}}
refresh();
</script>
""",
    )

def job_page(job_id: str) -> str:
    content = """
<section class="panel">
  <div class="row">
    <a class="btn secondary" href="/">返回上传</a>
    <span>任务 ID: <code>__JOB_ID_HTML__</code></span>
    <span class="status queued" id="status">读取中</span>
  </div>
  <div class="hint" id="message"></div>
  <div class="row" id="actions" style="margin-top:10px"></div>
</section>
<section class="panel" id="selectPanel" style="display:none">
  <h2 style="font-size:16px;margin:0 0 10px">选择生成范围</h2>
  <form id="generateForm">
    <div class="row" style="margin-bottom:10px">
      <label>开始 <input id="startInput" name="start" type="text" style="width:170px" placeholder="YYYY-MM-DD HH:MM:SS"></label>
      <label>结束 <input id="endInput" name="end" type="text" style="width:170px" placeholder="YYYY-MM-DD HH:MM:SS"></label>
      <button type="submit">生成选中范围</button>
    </div>
    <div class="hint" id="previewMeta"></div>
    <table style="margin-top:10px"><thead><tr><th><input id="selectAllSymbols" type="checkbox" checked></th><th>产品</th><th>订单数</th><th>时间范围</th><th>Profit</th></tr></thead><tbody id="symbolRows"></tbody></table>
  </form>
</section>
<section class="panel" id="previewPanel" style="display:none">
  <iframe class="chartFrame" id="chartFrame"></iframe>
</section>
<section class="panel">
  <h2 style="font-size:16px;margin:0 0 10px">任务日志</h2>
  <pre id="logs"></pre>
</section>
<section class="panel" id="filesPanel" style="display:none">
  <h2 style="font-size:16px;margin:0 0 10px">输出文件</h2>
  <table><thead><tr><th>文件</th><th>时间</th><th>大小</th><th>链接</th></tr></thead><tbody id="files"></tbody></table>
</section>
<script>
const jobId = __JOB_ID_JSON__;
let lastPreviewKey = '';
function renderSelection(job) {
  const preview = job.preview;
  if (!preview || job.status !== 'parsed') return;
  const key = JSON.stringify(preview);
  document.getElementById('selectPanel').style.display = 'block';
  if (key === lastPreviewKey) return;
  lastPreviewKey = key;
  document.getElementById('startInput').value = preview.start || '';
  document.getElementById('endInput').value = preview.end || '';
  document.getElementById('previewMeta').textContent =
    `账户 ${preview.account}，共 ${preview.trade_count} 笔，完整范围 ${preview.start} 到 ${preview.end}`;
  document.getElementById('symbolRows').innerHTML = (preview.symbols || []).map(s =>
    `<tr><td><input class="symbolCheck" type="checkbox" value="${s.symbol}" checked></td>` +
    `<td>${s.symbol}</td><td>${s.trades}</td><td>${s.open_start} - ${s.close_end}</td>` +
    `<td>${Number(s.profit || 0).toFixed(2)}</td></tr>`
  ).join('');
}
document.addEventListener('change', ev => {
  if (ev.target && ev.target.id === 'selectAllSymbols') {
    document.querySelectorAll('.symbolCheck').forEach(cb => cb.checked = ev.target.checked);
  }
});
document.addEventListener('submit', async ev => {
  if (ev.target && ev.target.id === 'generateForm') {
    ev.preventDefault();
    const symbols = Array.from(document.querySelectorAll('.symbolCheck:checked')).map(cb => cb.value);
    if (!symbols.length) { alert('请至少选择一个产品'); return; }
    const body = new URLSearchParams();
    body.set('job_id', jobId);
    symbols.forEach(s => body.append('symbols', s));
    body.set('start', document.getElementById('startInput').value.trim());
    body.set('end', document.getElementById('endInput').value.trim());
    const res = await fetch('/generate', {method:'POST', body});
    if (!res.ok) {
      const txt = await res.text();
      alert('启动生成失败：' + txt);
      return;
    }
    document.getElementById('selectPanel').style.display = 'none';
    setTimeout(refresh, 300);
  }
});
async function refresh(){
  const res = await fetch('/api/jobs/' + jobId, {cache:'no-store'});
  const job = await res.json();
  const status = document.getElementById('status');
  status.textContent = job.status || 'unknown';
  status.className = 'status ' + (job.status || '');
  document.getElementById('message').textContent = job.message || '';
  document.getElementById('logs').textContent = job.logs || '';
  renderSelection(job);
  if (job.html_url) {
    document.getElementById('actions').innerHTML =
      `<a class="btn" target="_blank" href="${job.html_url}">打开图表</a>` +
      `<a class="btn secondary" target="_blank" href="${job.html_file_url}">file 打开</a>`;
    document.getElementById('previewPanel').style.display = 'block';
    const frame = document.getElementById('chartFrame');
    if (!frame.src) frame.src = job.html_url;
  }
  if (job.files && job.files.length) {
    document.getElementById('filesPanel').style.display = 'block';
    document.getElementById('files').innerHTML = job.files.map(f =>
      `<tr><td>${f.name}</td><td>${f.mtime}</td><td>${f.size.toLocaleString()}</td>` +
      `<td><a target="_blank" href="${f.url}">网页</a> · <a target="_blank" href="${f.file_url}">file</a></td></tr>`
    ).join('');
  }
  if (!['done','failed','parsed'].includes(job.status)) setTimeout(refresh, 1200);
}
refresh();
</script>
"""
    content = content.replace("__JOB_ID_HTML__", html.escape(job_id)).replace("__JOB_ID_JSON__", json.dumps(job_id))
    return page_shell("生成任务", content)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{now_text()}] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            html_response(self, index_page())
            return
        if path.startswith("/job/"):
            job_id = path.rsplit("/", 1)[-1]
            html_response(self, job_page(job_id))
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or recover_job(job_id))
            json_response(self, job, 200 if job.get("status") != "missing" else 404)
            return
        if path == "/api/recent":
            json_response(self, {"charts": recent_charts()})
            return
        if path.startswith("/output/"):
            self.serve_output(unquote(path[len("/output/") :]))
            return
        html_response(self, page_shell("404", "<section class='panel'>页面不存在</section>"), 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/generate":
            self.handle_generate()
            return
        if path != "/upload":
            json_response(self, {"error": "not found"}, 404)
            return
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        item = form["statement"] if "statement" in form else None
        if item is None or not getattr(item, "filename", ""):
            html_response(self, page_shell("上传失败", "<section class='panel failed'>没有收到文件</section>"), 400)
            return
        filename = safe_filename(item.filename)
        if not filename.lower().endswith((".htm", ".html")):
            html_response(self, page_shell("上传失败", "<section class='panel failed'>只支持 .htm / .html</section>"), 400)
            return
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        dest = UPLOAD_DIR / f"{job_id}_{filename}"
        with dest.open("wb") as out:
            shutil.copyfileobj(item.file, out)
        with JOBS_LOCK:
            JOBS[job_id] = {
                "id": job_id,
                "status": "queued",
                "message": "已上传，等待生成",
                "created_at": now_text(),
                "updated_at": now_text(),
                "statement": str(dest),
                "logs": "",
            }
        thread = threading.Thread(target=run_inspection, args=(job_id, dest), daemon=True)
        thread.start()
        self.send_response(303)
        self.send_header("Location", f"/job/{job_id}")
        self.end_headers()

    def handle_generate(self) -> None:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        job_id = str(form.getfirst("job_id", "")).strip()
        symbols = [str(item).strip().upper() for item in form.getlist("symbols") if str(item).strip()]
        start = str(form.getfirst("start", "")).strip()
        end = str(form.getfirst("end", "")).strip()
        if not job_id:
            json_response(self, {"error": "missing job_id"}, 400)
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                job = recover_job(job_id)
                JOBS[job_id] = job
            statement = Path(str(job.get("statement", "")))
            status = job.get("status")
        if status not in {"parsed", "failed", "done"}:
            json_response(self, {"error": f"job is not ready for generation: {status}"}, 400)
            return
        if not statement.exists():
            json_response(self, {"error": "statement file not found"}, 404)
            return
        if not symbols:
            json_response(self, {"error": "no symbols selected"}, 400)
            return
        update_job(job_id, status="queued", message="已提交生成任务", selected_symbols=symbols, selected_start=start, selected_end=end, logs="")
        thread = threading.Thread(target=run_generation, args=(job_id, statement, symbols, start, end), daemon=True)
        thread.start()
        json_response(self, {"ok": True, "job_id": job_id})

    def serve_output(self, filename: str) -> None:
        name = Path(filename).name
        path = OUT_DIR / name
        if not path.exists() or not path.is_file():
            self.send_error(404, "file not found")
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not GENERATOR.exists():
        raise SystemExit(f"missing generator: {GENERATOR}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"trade kline web running: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
