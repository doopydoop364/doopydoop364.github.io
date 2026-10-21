'use strict';
// Click the green flag, let the project run for a number of runtime steps and
// dump observable state (screen text as the renderer sees it, CPU state).
//   node run_flag.cjs <project.sb3> <steps> [json-commands]
// commands: {"before":{"vars":{name:val},"diag":[k...]}, "mid":{"steps":n,"vars":{...}}}
const lib = require('./vmlib.cjs');

function screenText(vm, listName, decode) {
    const l = lib.getList(vm, listName);
    const rows = [];
    for (let r = 0; r < 25; r++) {
        let s = '';
        for (let c = 0; c < 80; c++) s += decode(l, r * 80 + c);
        rows.push(s);
    }
    return rows;
}
const cp = n => (n >= 32 && n < 127) ? String.fromCharCode(n) : (n === 0 ? ' ' : '?');


const VARS = {
    insns: 'executed instructions', state: 'CPU_STATE (0=off 1=run 2=halted 3=fault)', paused: 'PAUSED',
    power: 'POWER', screenOn: 'SCREEN_ON', frames: 'frame counter', ips: 'IPS', fps: 'FPS', eip: 'EIP',
    menu: 'MENU_OPEN', req: 'REQ', diagMem: 'diag: record memory accesses', diagTrace: 'diag: record instruction trace',
};

function snapshot(vm, name) {
    const o = {name};
    for (const [k, n] of Object.entries(VARS)) o[k] = lib.getVar(vm, n);
    o.shadow = screenText(vm, 'SCR_SHADOW (composed cell last drawn)', (l, i) => cp(l[i] < 0 ? 32 : l[i] % 256));
    o.shadowAttr = lib.getList(vm, 'SCR_SHADOW (composed cell last drawn)').map(v => v < 0 ? -1 : Math.floor(v / 256));
    o.gpr = lib.getList(vm, 'GPR [EAX ECX EDX EBX ESP EBP ESI EDI zero]').slice();
    o.diagOn = lib.getList(vm, 'DIAG_ON (settings toggles)').slice();
    return o;
}

// node run_flag.cjs <sb3> '<script json>' ; script = [{set:{var:val}, diag:[k..], steps:n, snap:"x"}, ...]
(async () => {
    const [,, sb3, scriptArg] = process.argv;
    const script = JSON.parse(scriptArg);
    const vm = await lib.loadVM(sb3);
    const stepN = n => { for (let i = 0; i < n; i++) vm.runtime._step(); };
    vm.runtime.startHats('event_whenflagclicked');
    const snaps = [];
    const t0 = Date.now();
    for (const st of script) {
        for (const [k, v] of Object.entries(st.set || {})) lib.setVar(vm, k, v);
        const diag = lib.getList(vm, 'DIAG_ON (settings toggles)');
        for (const k of (st.diag || [])) diag[k - 1] = 1;
        stepN(st.steps || 1);
        if (st.snap) snaps.push(snapshot(vm, st.snap));
    }
    console.log(JSON.stringify({wallMs: Date.now() - t0, snaps}));
})().catch(e => { console.error(e); process.exit(1); });
