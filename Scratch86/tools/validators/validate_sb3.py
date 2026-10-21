"""Structural validator for generated .sb3 files.

A valid ZIP is not a working Scratch program, so this checks the block graph:

* project.json parses; every costume asset exists in the archive with a matching MD5;
* every block's `parent` / `next` / input references point at existing blocks,
  and parent/child links agree (no dangling or orphaned blocks);
* every variable, list and broadcast referenced exists;
* every procedure call matches a procedure definition (proccode + argument ids);
* the archive contains executable scripts (hat blocks) - it is not an empty shell.

It then runs the official `scratch-parser` (the schema validator used by the
Scratch editor) through tools/validators/parse_sb3.cjs.

    python tools/validators/validate_sb3.py builds/Scratch86-v0.1.sb3
"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

HATS = {"event_whenflagclicked", "event_whenbroadcastreceived", "event_whenkeypressed",
        "event_whenthisspriteclicked", "event_whenstageclicked", "procedures_definition"}


def validate(path):
    errors = []
    zf = zipfile.ZipFile(path)
    if zf.testzip() is not None:
        errors.append("corrupt zip member")
    proj = json.loads(zf.read("project.json"))
    names = set(zf.namelist())
    stage = next(t for t in proj["targets"] if t["isStage"])
    gvars = set(stage["variables"])
    glists = set(stage["lists"])
    gbc = set(stage["broadcasts"])
    total_blocks = hats = 0
    for t in proj["targets"]:
        for c in t["costumes"]:
            if c["md5ext"] not in names:
                errors.append("%s: missing asset %s" % (t["name"], c["md5ext"]))
            else:
                md5 = hashlib.md5(zf.read(c["md5ext"])).hexdigest()
                if md5 != c["assetId"] or c["md5ext"] != md5 + "." + c["dataFormat"]:
                    errors.append("%s: asset hash mismatch %s" % (t["name"], c["md5ext"]))
        blocks = t["blocks"]
        vars_ok = gvars | set(t["variables"])
        lists_ok = glists | set(t["lists"])
        protos = {}
        for bid, b in blocks.items():
            if b["opcode"] == "procedures_prototype":
                protos[b["mutation"]["proccode"]] = json.loads(b["mutation"]["argumentids"])
        referenced = set()
        for bid, b in blocks.items():
            total_blocks += 1
            if b["opcode"] in HATS:
                hats += 1
            if b["topLevel"]:
                if b["parent"] is not None:
                    errors.append("%s/%s: top-level block has parent" % (t["name"], bid))
            elif b["parent"] not in blocks:
                errors.append("%s/%s: parent %r missing" % (t["name"], bid, b["parent"]))
            nxt = b["next"]
            if nxt is not None:
                referenced.add(nxt)
                if nxt not in blocks:
                    errors.append("%s/%s: next %r missing" % (t["name"], bid, nxt))
                elif blocks[nxt]["parent"] != bid:
                    errors.append("%s/%s: next block's parent mismatch" % (t["name"], bid))
            for iname, inp in b["inputs"].items():
                for ref in inp[1:]:
                    if isinstance(ref, str):
                        referenced.add(ref)
                        if ref not in blocks:
                            errors.append("%s/%s: input %s -> missing block %s" % (t["name"], bid, iname, ref))
                    elif isinstance(ref, list):
                        if ref[0] == 12 and ref[2] not in vars_ok:
                            errors.append("%s/%s: unknown variable %s" % (t["name"], bid, ref[1]))
                        if ref[0] == 13 and ref[2] not in lists_ok:
                            errors.append("%s/%s: unknown list %s" % (t["name"], bid, ref[1]))
                        if ref[0] == 11 and ref[2] not in gbc:
                            errors.append("%s/%s: unknown broadcast %s" % (t["name"], bid, ref[1]))
            for fname, f in b["fields"].items():
                if fname == "VARIABLE" and f[1] not in vars_ok:
                    errors.append("%s/%s: unknown variable %s" % (t["name"], bid, f[0]))
                if fname == "LIST" and f[1] not in lists_ok:
                    errors.append("%s/%s: unknown list %s" % (t["name"], bid, f[0]))
                if fname == "BROADCAST_OPTION" and f[1] not in gbc:
                    errors.append("%s/%s: unknown broadcast %s" % (t["name"], bid, f[0]))
            if b["opcode"] == "procedures_call":
                m = b["mutation"]
                ids = json.loads(m["argumentids"])
                if m["proccode"] not in protos:
                    errors.append("%s/%s: call to undefined procedure %r" % (t["name"], bid, m["proccode"]))
                elif protos[m["proccode"]] != ids:
                    errors.append("%s/%s: argument ids differ from definition (%s)" % (t["name"], bid, m["proccode"]))
                elif set(b["inputs"]) != set(ids):
                    errors.append("%s/%s: call inputs do not match arguments (%s)" % (t["name"], bid, m["proccode"]))
        for bid, b in blocks.items():
            if not b["topLevel"] and bid not in referenced and not b.get("shadow"):
                errors.append("%s/%s: orphan block (%s)" % (t["name"], bid, b["opcode"]))
    if hats == 0:
        errors.append("project contains no scripts")
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run(["node", os.path.join(here, "parse_sb3.cjs"), path], capture_output=True, text=True,
                       cwd=os.path.join(here, "..", ".."))
    if r.returncode != 0:
        errors.append("scratch-parser: " + (r.stdout + r.stderr).strip()[:500])
    return errors, {"blocks": total_blocks, "scripts": hats, "targets": len(proj["targets"])}


if __name__ == "__main__":
    errs, info = validate(sys.argv[1])
    print("blocks=%(blocks)d scripts=%(scripts)d targets=%(targets)d" % info)
    for e in errs[:50]:
        print("ERROR", e)
    print("OK" if not errs else "FAILED (%d errors)" % len(errs))
    sys.exit(1 if errs else 0)
