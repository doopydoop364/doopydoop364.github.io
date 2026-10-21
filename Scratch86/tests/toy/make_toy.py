import sys
sys.path.insert(0, "tools/sb3_generator")
from sb3gen import *

p = Project()
p.stage.costume("blank", BLANK_SVG, 240, 180)
x = p.var("X")
y = p.var("Y")
L = p.list("L", [0, 0, 0, 0])
add = p.procedure("add2", ["a", "b"], warp=True)
with add.body() as (b, a, bb):
    with b.if_(a < bb) as t:
        t.set(x, a + bb)
    with b.else_() as t:
        t.set(x, a - bb)
    b.replace(L, 2, x)
    b.set(y, L[2] * 2)
with p.on_broadcast("go") as b:
    b.call(add, 3, 10)
    b.change(y, 1)
p.save(sys.argv[1])
