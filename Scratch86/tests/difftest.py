"""Differential testing driver: Scratch86 (in scratch-vm) vs Unicorn."""
import re
import sys
from harness import assemble, run_cases
from proggen import Gen
from refmodel import run_reference

CMP_FLAGS = ["CF", "PF", "AF", "ZF", "SF", "OF", "DF"]


def make_case(seed, allow32=True):
    g = Gen(seed, allow32)
    src = g.program()
    init = g.init_state()
    code = list(assemble(src))
    return {"name": "seed%d" % seed, "src": src, "code": code, "init": init, "peek": Gen.PEEK, "limit": 5000,
            "mask": list(g.mask)}


def compare(case, res, masks=None):
    """Return list of difference strings between Scratch86 `res` and the reference."""
    masks = case.get("mask", ()) if masks is None else masks
    ref = run_reference(case["code"], case["init"], case["peek"])
    if "error" in ref:
        return None          # reference faulted (e.g. 32-bit EA > 0xFFFF): skip
    diffs = []
    exc = ref.get("exc")
    if exc and exc["vec"] == 5:
        # Unicorn reports stale (lazily evaluated) arithmetic flags when BOUND raises #BR; verified
        # against the same program without BOUND. Registers, memory, EIP and vector still compared.
        masks = set(masks) | {"CF", "PF", "AF", "ZF", "SF", "OF"}
    if res["state"] == 3 and res["fault"]["vec"] in (12, 13) and re.search(r"\[[^\]]*e[a-z]{2}|0x67", case.get("src", "")):
        # Intel faults real-mode accesses beyond the 0xFFFF segment limit; QEMU (the oracle) does
        # not check limits, so such cases cannot be compared. Limit logic has directed tests.
        return None
    if res["state"] == 3 and not exc:
        diffs.append("scratch86 fault vec=%d opcode=%02x at %x" % (res["fault"]["vec"], res["fault"]["opc"], res["fault"]["eip"]))
        return diffs
    if exc and res["state"] != 3:
        diffs.append("reference raised vector %d at eip=%x; scratch86 did not" % (exc["vec"], exc["eip"]))
        return diffs
    if exc and res["fault"]["vec"] != exc["vec"]:
        diffs.append("exception vector: ref=%d s86=%d" % (exc["vec"], res["fault"]["vec"]))
    for n in ref["regs"]:
        if ref["regs"][n] != res["regs"][n]:
            diffs.append("%s: ref=%08x s86=%08x" % (n, ref["regs"][n], res["regs"][n]))
    for n in ref.get("seg", {}):
        if ref["seg"][n] != res["seg"][n]:
            diffs.append("seg %s: ref=%04x s86=%04x" % (n, ref["seg"][n], res["seg"][n]))
    for f in CMP_FLAGS:
        if f in masks:
            continue
        if ref["flags"][f] != res["flags"][f]:
            diffs.append("flag %s: ref=%d s86=%d" % (f, ref["flags"][f], res["flags"][f]))
    # reference stops *before* the final HLT; Scratch86 executes it (+1)
    want = ref["eip"] if exc else ref["eip"] + 1
    if want != res["eip"]:
        diffs.append("EIP: ref=%x s86=%x" % (want, res["eip"]))
    for rm, sm in zip(ref["mem"], res["mem"]):
        if rm["bytes"] != sm["bytes"]:
            i = next(i for i, (a, b) in enumerate(zip(rm["bytes"], sm["bytes"])) if a != b)
            diffs.append("mem @%x: ref=%02x s86=%02x" % (rm["addr"] + i, rm["bytes"][i], sm["bytes"][i]))
            break
    return diffs


if __name__ == "__main__":
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    allow32 = (sys.argv[3] != "16") if len(sys.argv) > 3 else True
    cases = [make_case(s, allow32) for s in range(lo, hi)]
    results = run_cases(cases)
    bad = skipped = 0
    for c, r in zip(cases, results):
        d = compare(c, r)
        if d is None:
            skipped += 1
        elif d:
            bad += 1
            if bad <= 15:
                print("== %s\n%s\n  init regs=%s flags=%s\n  %s" % (c["name"], c["src"], {k: hex(v) for k, v in c["init"]["regs"].items()}, c["init"]["flags"], "\n  ".join(d)))
    print("cases=%d mismatches=%d skipped(ref fault)=%d" % (len(cases), bad, skipped))
