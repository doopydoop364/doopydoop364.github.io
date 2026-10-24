#!/usr/bin/env python3
import os,sys,subprocess,html,pathlib,shutil
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
BASE=pathlib.Path(__file__).resolve().parent
G=[]
for line in (BASE/"catalog.txt").read_text().splitlines():
    a=line.split("|")
    if len(a)==5:
        name,pkg,cat,icon,desc=a
        G.append(dict(name=name,pkg=pkg,cat=cat,icon=icon,desc=desc))
def check(pkg,flag):
    return subprocess.run(["pacman",flag,pkg],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
def select(pkg):
    return next((g for g in G if g["pkg"]==pkg),None)
def info(g):
    print("\n"+g["icon"]+" "+g["name"]+" ("+g["cat"]+")")
    print(g["desc"])
    pkg=g["pkg"]
    print("Package:",pkg)
    if check(pkg,"-Q"):print("Already installed")
    elif check(pkg,"-Si"):print("Available from configured repository")
    else:print("Not available in configured repositories; no install attempted")
def install(g):
    info(g)
    if check(g["pkg"],"-Q"):return
    if not check(g["pkg"],"-Si"):return
    if input("Install via sudo pacman? [y/N] ").lower().strip()=="y":
        subprocess.run(["sudo","pacman","-S","--needed",g["pkg"]])
def menu():
    found=G[:]
    while True:
        print("\nARCH86 GAMES — "+str(len(G))+" curated games")
        for n,g in enumerate(found[:25],1): print("%2d. %s %s [%s]"%(n,g["icon"],g["name"],g["cat"]))
        print("Search [s], show all [a], open gallery [w], quit [q]")
        v=input("> ").strip().lower()
        if v=="q":return
        if v=="w":gallery();return
        if v=="a": found=G[:];continue
        if v=="s" or not v.isdigit():
            term=(input("Search: ") if v=="s" else v).lower()
            found=[g for g in G if term in (g["name"]+" "+g["cat"]+" "+g["desc"]).lower()]
            continue
        n=int(v)
        if not 1<=n<=min(25,len(found)):continue
        g=found[n-1];info(g)
        if input("[i]nstall or [b]ack? ").strip().lower()=="i":install(g)
def gallery():
    cards=[]
    colors=["#64b5f6","#84cc16","#c084fc","#f59e0b","#2dd4bf","#fb7185"]
    for i,g in enumerate(G):
        safe=lambda s:html.escape(s,quote=True)
        cmd="arch86-games install "+g["pkg"]
        cards.append('<article data-search="'+safe((g["name"]+" "+g["cat"]+" "+g["desc"]).lower())+'" data-cat="'+safe(g["cat"])+'"><div class="thumb" style="--color:'+colors[i%len(colors)]+'"><span class="art">'+safe(g["icon"])+'</span><strong>'+safe(g["name"])+'</strong></div><div class="details"><small>'+safe(g["cat"])+'</small><h2>'+safe(g["name"])+'</h2><p>'+safe(g["desc"])+'</p><code>'+safe(g["pkg"])+'</code><button data-cmd="'+safe(cmd)+'">Copy install command</button></div></article>')
    opts="".join("<option>"+html.escape(x)+"</option>" for x in sorted({g["cat"] for g in G}))
    page='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Arch86 Games</title><style>
    *{box-sizing:border-box}body{background:#0b1220;color:#eef1f8;font:16px system-ui;margin:0}header{padding:34px 5vw;background:#182640}main{padding:24px 5vw}h1{margin:0}header p{color:#b4bfd4}input,select{background:#1a2638;border:1px solid #42516c;border-radius:8px;color:white;font:inherit;padding:12px;margin-bottom:18px}input{width:65%;max-width:560px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:18px}article{border:1px solid #34445b;border-radius:14px;overflow:hidden;background:#1a2434}.thumb{height:155px;background:radial-gradient(circle at 80% 20%,var(--color),transparent 43%),linear-gradient(130deg,#243d59,#0f192b);display:flex;flex-direction:column;justify-content:space-between;padding:14px}.art{font-size:52px}.thumb strong{font-size:22px;text-shadow:0 2px 4px #111}.details{padding:14px;display:flex;flex-direction:column;min-height:185px}.details small{color:#97b9eb}.details h2{font-size:18px;margin:5px 0}.details p{font-size:14px;color:#b5bfd0;line-height:1.5;flex:1}code{font-size:12px;color:#87d5db}button{margin-top:12px;padding:10px;background:#2257b0;color:#fff;border:0;border-radius:8px;cursor:pointer}article[hidden]{display:none}footer{color:#9cacc6;padding:25px 5vw}
    </style></head><body><header><h1>🎮 Arch86 Games</h1><p>__TOTAL__ games • searchable catalogue • original category thumbnails (not screenshots)</p></header><main><input id="q" placeholder="Search names, genres, descriptions"><select id="cat"><option value="">All genres</option>__OPTS__</select><p id="count"></p><div class="grid">__CARDS__</div></main><footer>Run arch86-games in the VM terminal to install games. Availability is checked against your actual Arch Linux 32 repositories. Some games require additional assets or graphics acceleration.</footer><script>
    const q=document.querySelector("#q"),cat=document.querySelector("#cat"),items=[...document.querySelectorAll("article")];function filter(){let n=0;for(const el of items){const ok=el.dataset.search.includes(q.value.toLowerCase())&&(!cat.value||el.dataset.cat===cat.value);el.hidden=!ok;if(ok)n++}document.querySelector("#count").textContent=n+" matches"}q.oninput=filter;cat.onchange=filter;filter();document.querySelectorAll("button").forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.dataset.cmd);b.textContent="Copied!"}catch(e){prompt("Copy this command:",b.dataset.cmd)}})
    </script></body></html>'''.replace("__TOTAL__",str(len(G))).replace("__OPTS__",opts).replace("__CARDS__","".join(cards)).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/","/index.html"):self.send_error(404);return
            self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers();self.wfile.write(page)
    server=ThreadingHTTPServer(("127.0.0.1",8765),Handler)
    print("Open http://127.0.0.1:8765 from a browser INSIDE Arch86; Ctrl+C to stop.",flush=True)
    server.serve_forever()
if __name__=="__main__":
    action=sys.argv[1] if len(sys.argv)>1 else "menu"
    if action=="menu":menu()
    elif action=="web":gallery()
    elif action=="list":
        for g in G:print(g["pkg"],g["name"])
    elif action=="install" and len(sys.argv)>2:
        g=select(sys.argv[2])
        if g:install(g)
        else:print("Unknown game ID; run arch86-games list")
    else:print("Usage: arch86-games [menu|web|list|install <package>]")
