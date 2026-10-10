import os, ssl, imaplib, email
from email import policy
from email.utils import getaddresses
from contextlib import contextmanager
from mcp.server.fastmcp import FastMCP

ADDRESS = os.getenv("ATLAS_ADDRESS", "atlas@pcenthusiast.blog").lower()
FOLDER = os.getenv("ATLAS_FOLDER", "Atlas")
USERNAME = os.getenv("ICLOUD_IMAP_USER", "")
PASSWORD = os.getenv("ICLOUD_APP_PASSWORD", "")
mcp = FastMCP("Atlas Mail", host="127.0.0.1", port=4191, stateless_http=True, json_response=True)

@contextmanager
def mailbox():
    if not USERNAME or not PASSWORD: raise RuntimeError("iCloud credentials are not configured")
    if FOLDER.upper() == "INBOX" or "/" in FOLDER: raise RuntimeError("Use dedicated Atlas folder")
    c = imaplib.IMAP4_SSL("imap.mail.me.com", 993, ssl_context=ssl.create_default_context(), timeout=20)
    try:
        c.login(USERNAME, PASSWORD)
        result, _ = c.select('"' + FOLDER.replace('"', "") + '"', readonly=True)
        if result != "OK": raise RuntimeError("Atlas folder unavailable")
        yield c
    finally:
        try: c.logout()
        except Exception: pass

def fetch(c, uid):
    if not str(uid).isdigit(): return None
    result, data = c.uid("fetch", str(uid), "(BODY.PEEK[])")
    if result != "OK": return None
    raw = next((x[1] for x in data if isinstance(x, tuple) and isinstance(x[1], bytes)), None)
    if raw is None: return None
    message = email.message_from_bytes(raw, policy=policy.default)
    recipients = getaddresses([str(message.get(k, "")) for k in ("to", "cc", "delivered-to", "x-original-to", "envelope-to")])
    if ADDRESS not in {a.lower().strip() for _, a in recipients}: return None
    return message

def summary(uid, m):
    return {"uid":str(uid),"subject":str(m.get("subject",""))[:500],"from":str(m.get("from",""))[:500],"date":str(m.get("date",""))[:100],"to":str(m.get("to",""))[:500]}

def body(m):
    parts = m.walk() if m.is_multipart() else [m]
    for part in parts:
        if part.get_content_type()=="text/plain" and part.get_content_disposition()!="attachment":
            try: return part.get_content()[:20000]
            except Exception: pass
    return "(No plain-text body available)"

@mcp.tool()
def list_atlas_mail(limit: int=10) -> list[dict]:
    """Read-only list of recent messages addressed to Atlas in the Atlas folder."""
    result=[]
    with mailbox() as c:
        status, data = c.uid("search", None, "ALL")
        if status != "OK": raise RuntimeError("Search failed")
        for uid in reversed((data[0] or b"").split()[-150:]):
            m=fetch(c,uid.decode())
            if m: result.append(summary(uid.decode(),m))
            if len(result)>=max(1,min(limit,30)): break
    return result

@mcp.tool()
def read_atlas_mail(uid: str) -> dict:
    """Read one Atlas-addressed email without changing its read status."""
    with mailbox() as c:
        m=fetch(c,uid)
        return {**summary(uid,m),"body_text":body(m)} if m else {"error":"Not found or not addressed to Atlas"}

@mcp.tool()
def search_atlas_mail(subject_contains: str, limit: int=10) -> list[dict]:
    """Search the subject of messages addressed to Atlas in its folder."""
    needle=subject_contains.strip().lower()
    if not needle: return []
    result=[]
    with mailbox() as c:
        status,data=c.uid("search",None,"ALL")
        if status != "OK": raise RuntimeError("Search failed")
        for uid in reversed((data[0] or b"").split()[-250:]):
            m=fetch(c,uid.decode())
            if m and needle in str(m.get("subject","")).lower(): result.append(summary(uid.decode(),m))
            if len(result)>=max(1,min(limit,30)): break
    return result

if __name__=="__main__":
    mcp.run(transport="streamable-http")
