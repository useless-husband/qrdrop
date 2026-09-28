"""手機看到的網頁（單一檔案內含 CSS/JS，不載入任何外部資源）。"""

from __future__ import annotations

from html import escape
from typing import Iterable, Sequence
from urllib.parse import quote

from .util import human_size

_CSS = """
:root{color-scheme:light dark;--bg:#f6f6f4;--fg:#1c1c1a;--muted:#66665f;--card:#fff;--line:#deded8;--accent:#0b57d0;--accent-fg:#fff;--bad:#b3261e;--ok:#1b6e3c}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ececea;--muted:#a3a39b;--card:#1e1e1c;--line:#34342f;--accent:#8ab4f8;--accent-fg:#0b1b33;--bad:#f2b8b5;--ok:#8fd4a4}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif}
main{max-width:640px;margin:0 auto;padding:20px 16px 48px}
.brand{font-size:13px;color:var(--muted);margin:0 0 4px;letter-spacing:.02em}
h1{font-size:22px;line-height:1.3;margin:0 0 16px;font-weight:650}
ul{list-style:none;margin:0 0 20px;padding:0;background:var(--card);border:1px solid var(--line);border-radius:10px}
li{display:flex;gap:12px;align-items:baseline;justify-content:space-between;padding:12px 14px;border-top:1px solid var(--line)}
li:first-child{border-top:0}
li a,li span.n{min-width:0;overflow-wrap:anywhere;color:var(--fg)}
li a{text-decoration:underline;text-underline-offset:3px}
.size{color:var(--muted);font-size:14px;white-space:nowrap;font-variant-numeric:tabular-nums}
.btn{display:inline-block;border:0;border-radius:8px;background:var(--accent);color:var(--accent-fg);font:inherit;font-weight:600;padding:12px 18px;min-height:44px;text-decoration:none;cursor:pointer}
.btn[disabled]{opacity:.45;cursor:default}
.btn.plain{background:transparent;color:var(--fg);border:1px solid var(--line)}
p.note{color:var(--muted);font-size:14px;margin:16px 0 0}
input[type=file]{display:block;width:100%;margin:0 0 16px;padding:14px;background:var(--card);border:1px dashed var(--muted);border-radius:10px;color:var(--fg);font:inherit}
input[type=text],input[type=password],input[inputmode]{font:inherit;font-size:24px;letter-spacing:.3em;width:9em;padding:10px 12px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;margin:0 0 16px;display:block}
progress{width:100%;height:14px;margin:0 0 8px}
.msg{margin:16px 0 0;padding:12px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card)}
.msg.err{color:var(--bad);border-color:var(--bad)}
.msg.ok{color:var(--ok);border-color:var(--ok)}
[hidden]{display:none!important}
"""


def _page(title: str, body: str, nonce: str, script: str = "") -> str:
    js = f'<script nonce="{nonce}">{script}</script>' if script else ""
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f"<title>{escape(title)}</title><style nonce=\"{nonce}\">{_CSS}</style></head>"
        f'<body><main><p class="brand">qrdrop</p>{body}</main>{js}</body></html>'
    )


def download_page(files: Sequence[tuple], zip_name: str, nonce: str, max_list: int = 500) -> str:
    """files: [(顯示名稱, 大小位元組, 連結), ...]"""
    total = sum(f[1] for f in files)
    rows = []
    for name, size, href in files[:max_list]:
        rows.append(f'<li><a href="{escape(href, quote=True)}">{escape(name)}</a><span class="size">{human_size(size)}</span></li>')
    if len(files) > max_list:
        rows.append(f'<li><span class="n">還有 {len(files) - max_list} 個檔案，請用「下載全部」</span><span class="size"></span></li>')
    if len(files) == 1:
        head = "<h1>有 1 個檔案可以下載</h1>"
        action = f'<a class="btn" href="{escape(files[0][2], quote=True)}">下載（{human_size(total)}）</a>'
    else:
        head = f"<h1>有 {len(files)} 個檔案可以下載</h1>"
        action = f'<a class="btn" href="all.zip">全部下載（{escape(zip_name)}，{human_size(total)}）</a>'
    note = '<p class="note">這是區網內的 HTTP 連線，沒有加密。</p>'
    return _page("下載檔案", f'{head}<ul>{"".join(rows)}</ul>{action}{note}', nonce)


_UPLOAD_JS = r"""
const $=id=>document.getElementById(id);
const fmt=n=>n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KB':n<1073741824?(n/1048576).toFixed(1)+' MB':(n/1073741824).toFixed(2)+' GB';
const input=$('f'),list=$('list'),btn=$('go'),bar=$('bar'),stat=$('stat'),msg=$('msg'),form=$('form');
function show(cls,text){msg.hidden=false;msg.className='msg '+cls;msg.textContent=text}
input.addEventListener('change',()=>{
  list.textContent='';msg.hidden=true;bar.hidden=true;stat.textContent='';
  let total=0;
  for(const f of input.files){total+=f.size;const li=document.createElement('li');
    const n=document.createElement('span');n.className='n';n.textContent=f.name;
    const s=document.createElement('span');s.className='size';s.textContent=fmt(f.size);
    li.append(n,s);list.append(li)}
  list.hidden=!input.files.length;btn.disabled=!input.files.length;
  btn.textContent=input.files.length?'上傳 '+input.files.length+' 個檔案（'+fmt(total)+'）':'上傳';
});
form.addEventListener('submit',e=>{
  e.preventDefault();
  if(!input.files.length)return;
  const fd=new FormData();for(const f of input.files)fd.append('files',f,f.name);
  const x=new XMLHttpRequest();x.open('POST','upload');x.setRequestHeader('Accept','application/json');
  btn.disabled=true;input.disabled=true;bar.hidden=false;bar.value=0;msg.hidden=true;
  x.upload.onprogress=ev=>{if(ev.lengthComputable){bar.value=ev.loaded/ev.total*100;stat.textContent=fmt(ev.loaded)+' / '+fmt(ev.total)+'（'+Math.floor(ev.loaded/ev.total*100)+'%）'}};
  const done=()=>{input.disabled=false;input.value='';list.hidden=true;btn.textContent='上傳';btn.disabled=true};
  x.onload=()=>{let r={};try{r=JSON.parse(x.responseText)}catch(_){}
    if(x.status===200&&r.ok){bar.value=100;stat.textContent='';show('ok','已傳到電腦：'+r.saved.map(s=>s.name).join('、'));done()}
    else{show('err',(r&&r.error)||('上傳失敗（HTTP '+x.status+'）'));input.disabled=false;btn.disabled=false;bar.hidden=true}};
  x.onerror=()=>{show('err','連線中斷，請確認手機和電腦還在同一個 Wi-Fi，然後再試一次。');input.disabled=false;btn.disabled=false;bar.hidden=true};
  x.send(fd);
});
"""


def upload_page(max_size: int, nonce: str) -> str:
    body = (
        "<h1>傳檔案到電腦</h1>"
        '<form id="form" method="post" action="upload" enctype="multipart/form-data">'
        '<input id="f" type="file" name="files" multiple>'
        '<ul id="list" hidden></ul>'
        '<progress id="bar" max="100" value="0" hidden></progress><div id="stat" class="size"></div>'
        '<button id="go" class="btn" type="submit" disabled>上傳</button></form>'
        '<div id="msg" class="msg" hidden></div>'
        f'<p class="note">每個檔案上限 {human_size(max_size)}。這是區網內的 HTTP 連線，沒有加密。</p>'
    )
    return _page("上傳檔案", body, nonce, _UPLOAD_JS)


def pin_page(nonce: str, error: bool = False) -> str:
    err = '<div class="msg err">PIN 不對，請再試一次。</div>' if error else ""
    body = (
        "<h1>輸入 PIN</h1>"
        '<form method="post" action="pin">'
        '<input name="pin" type="password" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="off" autofocus required>'
        '<button class="btn" type="submit">確定</button></form>'
        f"{err}"
        '<p class="note">PIN 顯示在電腦的終端機上。</p>'
    )
    return _page("輸入 PIN", body, nonce)


def result_page(ok: bool, text: str, names: Iterable[str], nonce: str) -> str:
    items = "".join(f'<li><span class="n">{escape(n)}</span></li>' for n in names)
    cls = "ok" if ok else "err"
    body = f'<h1>{"上傳完成" if ok else "上傳失敗"}</h1><div class="msg {cls}">{escape(text)}</div>'
    if items:
        body += f"<ul>{items}</ul>"
    body += '<a class="btn plain" href="./">回上一頁</a>'
    return _page("上傳結果", body, nonce)


def href_for(index: int, name: str) -> str:
    return f"f/{index}/{quote(name, safe='')}"
