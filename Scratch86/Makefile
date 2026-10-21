PY=.venv/bin/python
SB3=builds/Scratch86-v0.1.sb3

.PHONY: setup build validate test bench demo
setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && npm ci
demo:
	$(PY) -c "import sys; sys.path.insert(0,'tools/assembler'); from asm import assemble; open('firmware/demo.bin','wb').write(assemble(open('firmware/demo.asm').read()))"
build: demo
	$(PY) src/scratch/build.py --out $(SB3)
validate: build
	$(PY) tools/validators/validate_sb3.py $(SB3)
test: build
	$(PY) -m pytest -q
bench: build
	mkdir -p builds/tmp && $(PY) tools/benchmarks/workloads.py builds/tmp 20000
	node tools/benchmarks/bench.cjs $(SB3) builds/tmp/bench_mixed.bin
