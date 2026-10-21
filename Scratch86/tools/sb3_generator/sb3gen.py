"""sb3gen: a small DSL that emits genuine Scratch 3.0 block graphs.

This is a development-time tool.  It never executes guest code; it only
produces `project.json` (blocks, variables, lists, custom blocks) and packages
it as a deterministic `.sb3` archive.

Usage sketch::

    p = Project()
    x = p.var("X")
    add = p.procedure("add_to_x", ["n"], warp=True)
    with add.body() as (b, n):
        b.set(x, x + n)
    with p.on_flag() as b:
        b.set(x, 0)
        b.call(add, 5)
    p.save("out.sb3")

Expressions are Python objects; arithmetic operators build Scratch operator
blocks.  Python comparison operators `<` and `>` build Scratch comparisons;
use `eq()`, `ne()`, `le()`, `ge()`, `AND()`, `OR()`, `NOT()` for the rest.
"""
import hashlib
import json
import zipfile

# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------


def _coerce(x):
    if isinstance(x, Expr):
        return x
    if isinstance(x, bool):
        return Num(1 if x else 0)
    if isinstance(x, (int, float)):
        return Num(x)
    if isinstance(x, str):
        return Str(x)
    raise TypeError("cannot use %r as a Scratch expression" % (x,))


def _numval(v):
    """Numeric literal as a JSON number so variables keep a numeric type."""
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        v = int(v)
    return v


class Expr:
    """Base class for reporter / boolean expressions."""

    is_bool = False

    def __add__(self, o): return _binop("operator_add", self, o)
    def __radd__(self, o): return _binop("operator_add", o, self)
    def __sub__(self, o): return _binop("operator_subtract", self, o)
    def __rsub__(self, o): return _binop("operator_subtract", o, self)
    def __mul__(self, o): return _binop("operator_multiply", self, o)
    def __rmul__(self, o): return _binop("operator_multiply", o, self)
    def __truediv__(self, o): return _binop("operator_divide", self, o)
    def __rtruediv__(self, o): return _binop("operator_divide", o, self)
    def __mod__(self, o): return _binop("operator_mod", self, o)
    def __rmod__(self, o): return _binop("operator_mod", o, self)
    def __floordiv__(self, o): return floor(_binop("operator_divide", self, o))
    def __neg__(self): return _binop("operator_subtract", 0, self)
    def __lt__(self, o): return _cmp("operator_lt", self, o)
    def __gt__(self, o): return _cmp("operator_gt", self, o)
    __hash__ = object.__hash__


def _fold(op, a, b):
    if op == "operator_add": return a + b
    if op == "operator_subtract": return a - b
    if op == "operator_multiply": return a * b
    if op == "operator_divide": return a / b if b else None
    if op == "operator_mod":
        if b == 0: return None
        r = a % b  # python modulo has the divisor's sign, like Scratch
        return r
    return None


def _binop(op, a, b):
    a, b = _coerce(a), _coerce(b)
    if isinstance(a, Num) and isinstance(b, Num):
        r = _fold(op, a.value, b.value)
        if r is not None and (isinstance(r, int) or r == int(r)):
            return Num(int(r))
    return Op(op, {"NUM1": a, "NUM2": b})


def _cmp(op, a, b):
    return Op(op, {"OPERAND1": _coerce(a), "OPERAND2": _coerce(b)}, is_bool=True)


class Num(Expr):
    def __init__(self, value):
        self.value = value


class Str(Expr):
    def __init__(self, value):
        self.value = value


class Op(Expr):
    def __init__(self, opcode, inputs, fields=None, is_bool=False, mutation=None):
        self.opcode = opcode
        self.inputs = inputs
        self.fields = fields or {}
        self.is_bool = is_bool
        self.mutation = mutation


class Var(Expr):
    def __init__(self, name, vid):
        self.name, self.id = name, vid


class ListRef:
    def __init__(self, name, lid):
        self.name, self.id = name, lid

    def __getitem__(self, idx):
        """`LIST[idx]` -> `item idx of LIST` (1-based, as in Scratch)."""
        return Op("data_itemoflist", {"INDEX": _coerce(idx)}, {"LIST": (self.name, self.id)})

    def length(self):
        return Op("data_lengthoflist", {}, {"LIST": (self.name, self.id)})


class Arg(Expr):
    def __init__(self, name):
        self.name = name


def eq(a, b): return _cmp("operator_equals", a, b)
def ne(a, b): return NOT(eq(a, b))
def le(a, b): return NOT(_cmp("operator_gt", a, b))
def ge(a, b): return NOT(_cmp("operator_lt", a, b))


def AND(*xs):
    r = xs[0]
    for x in xs[1:]:
        r = Op("operator_and", {"OPERAND1": r, "OPERAND2": x}, is_bool=True)
    return r


def OR(*xs):
    r = xs[0]
    for x in xs[1:]:
        r = Op("operator_or", {"OPERAND1": r, "OPERAND2": x}, is_bool=True)
    return r


def NOT(x):
    return Op("operator_not", {"OPERAND": x}, is_bool=True)


def mathop(name, x):
    x = _coerce(x)
    if isinstance(x, Num) and name == "floor":
        return Num(int(x.value // 1))
    return Op("operator_mathop", {"NUM": x}, {"OPERATOR": (name, None)})


def floor(x): return mathop("floor", x)
def join(a, b): return Op("operator_join", {"STRING1": _coerce(a), "STRING2": _coerce(b)})
def letter_of(i, s): return Op("operator_letter_of", {"LETTER": _coerce(i), "STRING": _coerce(s)})
def length_of(s): return Op("operator_length", {"STRING": _coerce(s)})
def reporter(opcode, inputs=None, fields=None): return Op(opcode, inputs or {}, fields or {})


# Which primitive shadow type to use per (opcode, input).  Anything not listed
# defaults to a number shadow (4) for Num and text (10) for Str.
_SLOT_TYPE = {
    ("operator_equals", "OPERAND1"): 10, ("operator_equals", "OPERAND2"): 10,
    ("operator_lt", "OPERAND1"): 10, ("operator_lt", "OPERAND2"): 10,
    ("operator_gt", "OPERAND1"): 10, ("operator_gt", "OPERAND2"): 10,
    ("operator_join", "STRING1"): 10, ("operator_join", "STRING2"): 10,
    ("operator_letter_of", "STRING"): 10, ("operator_length", "STRING"): 10,
    ("data_setvariableto", "VALUE"): 10,
    ("data_addtolist", "ITEM"): 10, ("data_replaceitemoflist", "ITEM"): 10,
    ("data_insertatlist", "ITEM"): 10,
    ("data_itemoflist", "INDEX"): 7, ("data_replaceitemoflist", "INDEX"): 7,
    ("data_deleteoflist", "INDEX"): 7, ("data_insertatlist", "INDEX"): 7,
    ("control_repeat", "TIMES"): 6,
}

# --------------------------------------------------------------------------
# Project structure
# --------------------------------------------------------------------------


class Target:
    def __init__(self, project, name, is_stage):
        self.project, self.name, self.is_stage = project, name, is_stage
        self.blocks = {}
        self.costumes = []
        self.visible = True
        self.x, self.y, self.size = 0, 0, 100
        self.layer = 0
        self._n = 0
        self._scripts = 0

    def _id(self):
        self._n += 1
        return "%s_b%d" % ("S" if self.is_stage else self.name[:3], self._n)

    def costume(self, name, svg_text, cx=0, cy=0):
        data = svg_text.encode("utf-8")
        md5 = hashlib.md5(data).hexdigest()
        self.costumes.append({
            "assetId": md5, "name": name, "md5ext": md5 + ".svg", "dataFormat": "svg",
            "rotationCenterX": cx, "rotationCenterY": cy, "bitmapResolution": 1,
        })
        self.project.assets[md5 + ".svg"] = data

    # ---- top level scripts ------------------------------------------------
    def _hat(self, opcode, fields=None, mutation=None):
        bid = self._id()
        col, row = self._scripts % 6, self._scripts // 6
        self._scripts += 1
        blk = {"opcode": opcode, "next": None, "parent": None, "inputs": {},
               "fields": fields or {}, "shadow": False, "topLevel": True,
               "x": col * 700, "y": row * 1600}
        if mutation:
            blk["mutation"] = mutation
        self.blocks[bid] = blk
        return Builder(self, bid)

    def on_flag(self):
        return _Script(self._hat("event_whenflagclicked"))

    def on_broadcast(self, name):
        bid = self.project.broadcast(name)
        return _Script(self._hat("event_whenbroadcastreceived", {"BROADCAST_OPTION": [name, bid]}))

    def on_key(self, key):
        return _Script(self._hat("event_whenkeypressed", {"KEY_OPTION": [key, None]}))

    def on_click(self):
        return _Script(self._hat("event_whenthisspriteclicked" if not self.is_stage
                                 else "event_whenstageclicked"))

    def procedure(self, name, args=(), warp=True):
        return Proc(self, name, list(args), warp)


class _Script:
    def __init__(self, builder): self.b = builder
    def __enter__(self): return self.b
    def __exit__(self, *a): return False


class Proc:
    """A custom block (procedure).  Declared first so calls may precede the body."""

    def __init__(self, target, name, args, warp):
        self.target, self.name, self.args, self.warp = target, name, args, warp
        self.proccode = name + "".join(" %s" for _ in args)
        key = hashlib.md5((target.name + "|" + name + "|" + str(len(args))).encode()).hexdigest()[:10]
        self.arg_ids = ["arg_%s_%d" % (key, i) for i in range(len(args))]
        self.defined = False
        if (target.name, name) in target.project._procs:
            raise ValueError("duplicate procedure %s" % name)
        target.project._procs[(target.name, name)] = self

    def mutation(self, call=False):
        m = {"tagName": "mutation", "children": [], "proccode": self.proccode,
             "argumentids": json.dumps(self.arg_ids), "warp": "true" if self.warp else "false"}
        if not call:
            m["argumentnames"] = json.dumps(self.args)
            m["argumentdefaults"] = json.dumps(["" for _ in self.args])
        return m

    def body(self):
        t = self.target
        if self.defined:
            raise ValueError("procedure %s defined twice" % self.name)
        self.defined = True
        proto = t._id()
        defid = t._id()
        pinputs = {}
        for name, aid in zip(self.args, self.arg_ids):
            rid = t._id()
            t.blocks[rid] = {"opcode": "argument_reporter_string_number", "next": None,
                             "parent": proto, "inputs": {}, "fields": {"VALUE": [name, None]},
                             "shadow": True, "topLevel": False}
            pinputs[aid] = [1, rid]
        t.blocks[proto] = {"opcode": "procedures_prototype", "next": None, "parent": defid,
                           "inputs": pinputs, "fields": {}, "shadow": True, "topLevel": False,
                           "mutation": self.mutation()}
        col, row = t._scripts % 6, t._scripts // 6
        t._scripts += 1
        t.blocks[defid] = {"opcode": "procedures_definition", "next": None, "parent": None,
                           "inputs": {"custom_block": [1, proto]}, "fields": {}, "shadow": False,
                           "topLevel": True, "x": col * 700, "y": row * 1600}
        b = Builder(t, defid)
        return _ProcBody(b, [Arg(n) for n in self.args])


class _ProcBody:
    def __init__(self, b, args): self.b, self.args = b, args
    def __enter__(self): return (self.b, *self.args)
    def __exit__(self, *a): return False


class Builder:
    """Appends statement blocks to a chain (script body or C-block substack)."""

    def __init__(self, target, head_id):
        self.t = target
        self.last = head_id      # id of the last block in this chain
        self.first = None        # id of first block emitted by this builder
        self.owner = head_id     # id used as `parent` for the first block

    # ---- plumbing ---------------------------------------------------------
    def _emit_input(self, opcode, name, value, parent):
        """Return the sb3 input array for `value`."""
        v = _coerce(value)
        slot = _SLOT_TYPE.get((opcode, name))
        if isinstance(v, Num):
            return [1, [slot or 4, _numval(v.value)]]
        if isinstance(v, Str):
            return [1, [10 if slot in (None, 10) else slot, v.value]]
        # block reporters
        default = [slot or 4, "0"] if slot != 10 else [10, ""]
        if v.is_bool:
            default = None
        if isinstance(v, Var):
            ref = [12, v.name, v.id]
            return [3, ref, default] if default else [2, ref]
        if isinstance(v, Arg):
            bid = self.t._id()
            self.t.blocks[bid] = {"opcode": "argument_reporter_string_number", "next": None,
                                  "parent": parent, "inputs": {}, "fields": {"VALUE": [v.name, None]},
                                  "shadow": False, "topLevel": False}
            return [3, bid, default]
        bid = self._emit_op(v, parent)
        return [2, bid] if v.is_bool else [3, bid, default]

    def _emit_op(self, v, parent):
        bid = self.t._id()
        blk = {"opcode": v.opcode, "next": None, "parent": parent, "inputs": {},
               "fields": {}, "shadow": False, "topLevel": False}
        self.t.blocks[bid] = blk
        for k, e in v.inputs.items():
            blk["inputs"][k] = self._emit_input(v.opcode, k, e, bid)
        for k, f in v.fields.items():
            blk["fields"][k] = list(f)
        if v.mutation:
            blk["mutation"] = v.mutation
        return bid

    def _stmt(self, opcode, inputs=None, fields=None, mutation=None):
        bid = self.t._id()
        blk = {"opcode": opcode, "next": None, "parent": self.last, "inputs": {},
               "fields": {}, "shadow": False, "topLevel": False}
        # link into the chain
        prev = self.t.blocks[self.last]
        if self._is_chain_head():
            self._link_head(bid)
        else:
            prev["next"] = bid
        self.t.blocks[bid] = blk
        self.last = bid
        for k, e in (inputs or {}).items():
            blk["inputs"][k] = self._emit_input(opcode, k, e, bid)
        for k, f in (fields or {}).items():
            blk["fields"][k] = list(f)
        if mutation:
            blk["mutation"] = mutation
        return bid

    def _is_chain_head(self):
        return False

    # ---- statements -------------------------------------------------------
    def set(self, var, value):
        self._stmt("data_setvariableto", {"VALUE": value}, {"VARIABLE": (var.name, var.id)})

    def change(self, var, value):
        self._stmt("data_changevariableby", {"VALUE": value}, {"VARIABLE": (var.name, var.id)})

    def add(self, lst, item):
        self._stmt("data_addtolist", {"ITEM": item}, {"LIST": (lst.name, lst.id)})

    def replace(self, lst, idx, item):
        self._stmt("data_replaceitemoflist", {"INDEX": idx, "ITEM": item}, {"LIST": (lst.name, lst.id)})

    def delete_all(self, lst):
        self._stmt("data_deletealloflist", {}, {"LIST": (lst.name, lst.id)})

    def broadcast(self, name):
        bid = self.t.project.broadcast(name)
        self._stmt("event_broadcast", {}, None)
        blk = self.t.blocks[self.last]
        blk["inputs"]["BROADCAST_INPUT"] = [1, [11, name, bid]]

    def raw(self, opcode, inputs=None, fields=None, mutation=None):
        """Escape hatch for blocks without a dedicated helper (pen, looks...)."""
        return self._stmt(opcode, inputs, fields, mutation)

    def call(self, proc, *args):
        if len(args) != len(proc.args):
            raise ValueError("%s expects %d args, got %d" % (proc.name, len(proc.args), len(args)))
        bid = self.t._id()
        blk = {"opcode": "procedures_call", "next": None, "parent": self.last, "inputs": {},
               "fields": {}, "shadow": False, "topLevel": False, "mutation": proc.mutation(call=True)}
        if self._is_chain_head():
            self._link_head(bid)
        else:
            self.t.blocks[self.last]["next"] = bid
        self.t.blocks[bid] = blk
        self.last = bid
        for aid, e in zip(proc.arg_ids, args):
            blk["inputs"][aid] = self._emit_input("procedures_call", aid, e, bid)

    def stop_script(self):
        self._stmt("control_stop", {}, {"STOP_OPTION": ("this script", None)},
                   {"tagName": "mutation", "children": [], "hasnext": "false"})

    def wait_until(self, cond):
        self._stmt("control_wait_until", {"CONDITION": cond})

    # ---- C blocks ---------------------------------------------------------
    def _c_block(self, opcode, inputs, substack_name="SUBSTACK"):
        bid = self._stmt(opcode, inputs)
        return _CBody(self, bid, substack_name)

    def if_(self, cond):
        return self._c_block("control_if", {"CONDITION": cond})

    def else_(self):
        """Use directly after a `with b.if_(...)` block to attach an else branch."""
        prev = self.t.blocks[self.last]
        if prev["opcode"] not in ("control_if", "control_if_else"):
            raise ValueError("else_ must follow if_")
        if prev["opcode"] == "control_if_else":
            raise ValueError("if already has an else")
        prev["opcode"] = "control_if_else"
        return _CBody(self, self.last, "SUBSTACK2", reuse=True)

    def while_not(self, cond):
        return self._c_block("control_repeat_until", {"CONDITION": cond})

    def repeat(self, n):
        return self._c_block("control_repeat", {"TIMES": n})

    def forever(self):
        return self._c_block("control_forever", {})

    # ---- helpers ----------------------------------------------------------
    def dispatch(self, key, cases, default=None):
        """Emit a binary decision tree on `key` (a Var/Arg).

        `cases`: list of (lo, hi, emit) with inclusive ranges, emit(b) emits
        the body.  Ranges must not overlap.  `default(b)` handles the rest.
        """
        segs = sorted(cases, key=lambda c: c[0])
        for a, c in zip(segs, segs[1:]):
            if a[1] >= c[0]:
                raise ValueError("overlapping dispatch ranges %r %r" % (a[:2], c[:2]))
        self._tree(key, segs, None, None, default)

    def _tree(self, key, segs, lo_known, hi_known, default):
        if not segs:
            if default:
                default(self)
            return
        if len(segs) == 1:
            lo, hi, emit = segs[0]
            conds = []
            if lo_known is None or lo > lo_known:
                conds.append(ge(key, lo) if lo != hi else None)
            if hi_known is None or hi < hi_known:
                conds.append(le(key, hi) if lo != hi else None)
            if lo == hi:
                if lo_known == lo and hi_known == hi:
                    conds = []
                else:
                    conds = [eq(key, lo)]
            conds = [c for c in conds if c is not None]
            if not conds:
                emit(self)
                return
            c = conds[0] if len(conds) == 1 else AND(*conds)
            if default:
                with self.if_(c) as bb:
                    emit(bb)
                with self.else_() as bb:
                    default(bb)
            else:
                with self.if_(c) as bb:
                    emit(bb)
            return
        mid = len(segs) // 2
        split = segs[mid][0]
        with self.if_(key < split) as bb:
            bb._tree(key, segs[:mid], lo_known, split - 1, default)
        with self.else_() as bb:
            bb._tree(key, segs[mid:], split, hi_known, default)


class _CBody:
    """Context manager for the substack of an if / loop; yields a Builder."""

    def __init__(self, parent, bid, substack_name, reuse=False):
        self.parent, self.bid, self.name = parent, bid, substack_name
        self.sub = _SubBuilder(parent.t, bid, substack_name)

    def __enter__(self):
        return self.sub

    def __exit__(self, *a):
        return False


class _SubBuilder(Builder):
    """Builder whose first emitted block hooks into a C-block's substack input."""

    def __init__(self, target, owner_id, input_name):
        super().__init__(target, owner_id)
        self.owner_id, self.input_name = owner_id, input_name
        self._empty = True

    def _is_chain_head(self):
        return self._empty

    def _link_head(self, bid):
        self._empty = False
        self.t.blocks[self.owner_id]["inputs"][self.input_name] = [2, bid]
        # parent of the first block is the C block; set after creation by _stmt

    def _stmt(self, opcode, inputs=None, fields=None, mutation=None):
        bid = super()._stmt(opcode, inputs, fields, mutation)
        return bid


class Project:
    def __init__(self):
        self.stage = Target(self, "Stage", True)
        self.targets = [self.stage]
        self.vars, self.lists = {}, {}
        self.broadcasts = {}
        self.assets = {}
        self._procs = {}
        self.extensions = set()

    def sprite(self, name, x=0, y=0, visible=True):
        t = Target(self, name, False)
        t.x, t.y, t.visible = x, y, visible
        t.layer = len(self.targets)
        self.targets.append(t)
        return t

    # stage shortcuts
    def on_flag(self): return self.stage.on_flag()
    def on_broadcast(self, n): return self.stage.on_broadcast(n)
    def procedure(self, name, args=(), warp=True): return self.stage.procedure(name, args, warp)

    def var(self, name, value=0):
        if name in self.vars:
            raise ValueError("duplicate variable %s" % name)
        vid = "v_" + hashlib.md5(name.encode()).hexdigest()[:16]
        self.vars[name] = (vid, value)
        return Var(name, vid)

    def list(self, name, items=()):
        if name in self.lists:
            raise ValueError("duplicate list %s" % name)
        lid = "l_" + hashlib.md5(name.encode()).hexdigest()[:16]
        self.lists[name] = (lid, list(items))
        return ListRef(name, lid)

    def broadcast(self, name):
        bid = "bc_" + hashlib.md5(name.encode()).hexdigest()[:16]
        self.broadcasts[bid] = name
        return bid

    # ---- serialisation ----------------------------------------------------
    def to_json(self):
        targets = []
        for t in self.targets:
            d = {"isStage": t.is_stage, "name": t.name,
                 "variables": {}, "lists": {}, "broadcasts": {},
                 "blocks": t.blocks, "comments": {},
                 "currentCostume": 0, "costumes": t.costumes, "sounds": [],
                 "volume": 100, "layerOrder": t.layer}
            if t.is_stage:
                d["variables"] = {vid: [n, val] for n, (vid, val) in self.vars.items()}
                d["lists"] = {lid: [n, items] for n, (lid, items) in self.lists.items()}
                d["broadcasts"] = dict(self.broadcasts)
                d.update({"tempo": 60, "videoTransparency": 50, "videoState": "on",
                          "textToSpeechLanguage": None})
            else:
                d.update({"visible": t.visible, "x": t.x, "y": t.y, "size": t.size,
                          "direction": 90, "draggable": False, "rotationStyle": "all around"})
            targets.append(d)
        return {"targets": targets, "monitors": [], "extensions": sorted(self.extensions),
                "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": "Scratch86 sb3gen"}}

    def check(self):
        for (tn, pn), pr in self._procs.items():
            if not pr.defined:
                raise ValueError("procedure %s declared but never defined" % pn)

    def save(self, path):
        self.check()
        write_sb3(path, json.dumps(self.to_json(), separators=(",", ":"), sort_keys=False), self.assets)


def write_sb3(path, project_json, assets):
    """Deterministic archive: fixed timestamps, stable member order."""
    def zi(name):
        z = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        z.compress_type = zipfile.ZIP_DEFLATED
        z.external_attr = 0o644 << 16
        return z
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zi("project.json"), project_json.encode("utf-8"))
        for name in sorted(assets):
            zf.writestr(zi(name), assets[name])


BLANK_SVG = ('<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="480" height="360" '
             'viewBox="0 0 480 360"><rect width="480" height="360" fill="#000000"/></svg>')
