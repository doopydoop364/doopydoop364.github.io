'use strict';
// Benchmark Scratch86 in a headless Scratch VM.
//   node bench.cjs <project.sb3> <workload.bin(hex file)> [label]
// Runs the workload through the Scratch-block CPU (s86_test_run) and reports
// wall-clock time, executed guest instructions and instructions per second.
// Set S86_VM=/path/to/scratch-vm to benchmark another VM build.
const fs = require('fs');
const lib = require('../vmrunner/vmlib.cjs');

(async () => {
    const [,, sb3, binPath, label] = process.argv;
    const code = Array.from(fs.readFileSync(binPath));
    const vm = await lib.loadVM(sb3);
    if (vm.setCompilerOptions) vm.setCompilerOptions({enabled: process.env.S86_COMPILER !== '0'});
    const image = lib.getList(vm, 'BOOT_IMAGE (loaded at 0000:7C00)');
    image.length = 0; code.forEach(b => image.push(b));
    lib.setVar(vm, 'test: instruction limit', 1e9);
    lib.setVar(vm, 'instruction budget per frame', parseInt(process.env.S86_BUDGET || '20000', 10));
    const runs = [];
    for (let i = 0; i < (parseInt(process.env.S86_RUNS || '3', 10)); i++) {
        const t0 = process.hrtime.bigint();
        lib.runBroadcast(vm, 's86_test_run', 1e9);
        const ms = Number(process.hrtime.bigint() - t0) / 1e6;
        const insns = lib.getVar(vm, 'executed instructions');
        runs.push({ms, insns, ips: Math.round(insns / (ms / 1000))});
    }
    console.log(JSON.stringify({label: label || process.env.S86_VM || 'scratch-vm (npm 5.0.300)', node: process.version, runs}));
})().catch(e => { console.error(e); process.exit(1); });
