'use strict';
// Runs test cases against a generated Scratch86 .sb3 inside the stock scratch-vm.
//   node run_cases.cjs <project.sb3> <cases.json> <out.json>
// Each case: {name, code:[bytes] (loaded at 0000:7C00 by the Scratch boot
// routine), init:{regs:{EAX:..}, flags:{CF:..}, seg:{DS:..}, eip, mem:[{addr,bytes}],
// vars:{NAME:val}}, limit, peek:[{addr,len}]}.  The harness only sets initial
// *state* (like loading a disk image or presetting registers) and reads results;
// every guest instruction is executed by the Scratch blocks.
const fs = require('fs');
const lib = require('./vmlib.cjs');

const GPR_NAMES = ['EAX', 'ECX', 'EDX', 'EBX', 'ESP', 'EBP', 'ESI', 'EDI'];
const SEG_NAMES = ['ES', 'CS', 'SS', 'DS', 'FS', 'GS'];
const FLAGS = ['CF', 'PF', 'AF', 'ZF', 'SF', 'TF', 'IF', 'DF', 'OF'];

function ramList(vm, bank) {
    return lib.getList(vm, 'RAM_' + String(bank).padStart(2, '0'));
}
function pokeByte(vm, addr, v) { ramList(vm, addr >> 16)[addr & 0xFFFF] = v & 0xFF; }
function peekByte(vm, addr) { const l = ramList(vm, addr >> 16); return l ? l[addr & 0xFFFF] : 0xFF; }

(async () => {
    const [,, sb3, casesPath, outPath] = process.argv;
    const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
    const vm = await lib.loadVM(sb3);
    const results = [];
    for (const c of cases) {
        const t0 = process.hrtime.bigint();
        const touched = [];
        const res = {name: c.name};
        try {
            if (c.memops) {            // memory-subsystem tests: no CPU involved
                res.memres = [];
                for (const [k, v] of Object.entries(c.vars || {})) lib.setVar(vm, k, v);
                for (const op of c.memops) {
                    lib.setVar(vm, 'test: memory op (1-3 read b/w/d, 4-6 write b/w/d)', op.op);
                    lib.setVar(vm, 'test: memory address', op.addr);
                    lib.setVar(vm, 'test: memory value', op.val || 0);
                    lib.runBroadcast(vm, 's86_test_memop');
                    res.memres.push(lib.getVar(vm, 'test: memory result'));
                }
                res.ram = (c.peek || []).map(pk => ({addr: pk.addr, bytes: Array.from({length: pk.len}, (_, i) => peekByte(vm, pk.addr + i))}));
                for (let k = 0; k < 17; k++) ramList(vm, k).fill(0);
                for (const k of Object.keys(c.vars || {})) lib.setVar(vm, k, 0);
                results.push(res);
                continue;
            }
            lib.setVar(vm, 'test: instruction limit', c.limit || 100000);
            lib.setVar(vm, 'instruction budget per frame', c.budget || 5000);
            const image = lib.getList(vm, 'BOOT_IMAGE (loaded at 0000:7C00)');
            image.length = 0;
            for (const b of (c.code || [])) image.push(b);
            lib.runBroadcast(vm, 's86_test_boot');
            const init = c.init || {};
            const gpr = lib.getList(vm, 'GPR [EAX ECX EDX EBX ESP EBP ESI EDI zero]');
            for (const [k, v] of Object.entries(init.regs || {})) gpr[GPR_NAMES.indexOf(k)] = v;
            for (const [k, v] of Object.entries(init.flags || {})) lib.setVar(vm, k, v);
            const seg = lib.getList(vm, 'SEG [ES CS SS DS FS GS]');
            const segb = lib.getList(vm, 'SEGBASE [ES CS SS DS FS GS] (hidden base)');
            for (const [k, v] of Object.entries(init.seg || {})) {
                const i = SEG_NAMES.indexOf(k); seg[i] = v; segb[i] = v * 16;
            }
            if (init.eip !== undefined) lib.setVar(vm, 'EIP', init.eip);
            for (const [k, v] of Object.entries(init.vars || {})) lib.setVar(vm, k, v);
            for (const m of (init.mem || [])) {
                m.bytes.forEach((b, i) => { pokeByte(vm, m.addr + i, b); touched.push(m.addr + i); });
            }
            for (let i = 0; i < (c.code || []).length; i++) touched.push(0x7C00 + i);
            const steps = lib.runBroadcast(vm, 's86_test_exec', 10000000);
            res.steps = steps;
            res.regs = {};
            GPR_NAMES.forEach((n, i) => res.regs[n] = gpr[i]);
            res.seg = {}; SEG_NAMES.forEach((n, i) => res.seg[n] = seg[i]);
            res.flags = {}; FLAGS.forEach(f => res.flags[f] = lib.getVar(vm, f));
            res.eip = lib.getVar(vm, 'EIP');
            res.state = lib.getVar(vm, 'CPU_STATE (0=off 1=run 2=halted 3=fault)');
            res.cr0 = lib.getVar(vm, 'CR0');
            res.shutdown = lib.getVar(vm, 'CPU shut down (triple fault)');
            res.cpl = lib.getVar(vm, 'current privilege level');
            res.fault = {vec: lib.getVar(vm, 'FAULT vector (6=#UD 0=#DE)'),
                         opc: lib.getVar(vm, 'FAULT opcode'), eip: lib.getVar(vm, 'FAULT instruction EIP'),
                         err: lib.getVar(vm, 'error code of the first (original) exception')};
            res.insns = lib.getVar(vm, 'executed instructions');
            res.mem = [];
            for (const pk of (c.peek || [])) {
                const bytes = [];
                for (let i = 0; i < pk.len; i++) bytes.push(peekByte(vm, pk.addr + i));
                res.mem.push({addr: pk.addr, bytes});
                for (let i = 0; i < pk.len; i++) touched.push(pk.addr + i);
            }
            res.diag = {};
            for (const [k, n] of Object.entries({
                lastFetch: 'last instruction fetch (linear)', lastOpcode: 'last opcode',
                lastRdLin: 'last read linear', lastRdPhys: 'last read physical',
                lastWrLin: 'last write linear', lastWrPhys: 'last write physical',
                lastVal: 'last data access value', lastW: 'last data access width'})) {
                res.diag[k] = lib.getVar(vm, n);
            }
        } catch (e) {
            res.error = String(e && e.stack || e);
        }
        // guests may write anywhere; wipe the low banks so cases stay independent
        for (let k = 0; k < 5; k++) ramList(vm, k).fill(0);
        res.ms = Number(process.hrtime.bigint() - t0) / 1e6;
        results.push(res);
    }
    fs.writeFileSync(outPath, JSON.stringify(results));
})().catch(e => { console.error(e); process.exit(1); });
