'use strict';
// Headless scratch-vm helpers used by tests and benchmarks.  This is a
// TEST/DEV tool: it loads a generated .sb3 into the stock Scratch VM and
// reads the resulting variable / list state.  It never executes guest code
// itself; all guest execution happens in the Scratch blocks.
const fs = require('fs');
const path = require('path');
const VirtualMachine = require('scratch-vm');
const _ss = require('scratch-storage');
const ScratchStorage = _ss.ScratchStorage;

async function loadVM(sb3Path) {
    const vm = new VirtualMachine();
    const storage = new ScratchStorage();
    vm.attachStorage(storage);
    const buf = fs.readFileSync(sb3Path);
    await vm.loadProject(buf);
    vm.runtime.currentStepTime = 1000 / 30; // stepThreads budget is NaN without this
    return vm;
}

function stage(vm) { return vm.runtime.getTargetForStage(); }

function getVar(vm, name) {
    const v = Object.values(stage(vm).variables).find(x => x.name === name && x.type === '');
    if (!v) throw new Error('no variable ' + name);
    return v.value;
}
function setVar(vm, name, value) {
    const v = Object.values(stage(vm).variables).find(x => x.name === name && x.type === '');
    if (!v) throw new Error('no variable ' + name);
    v.value = value;
}
function getList(vm, name) {
    const v = Object.values(stage(vm).variables).find(x => x.name === name && x.type === 'list');
    if (!v) throw new Error('no list ' + name);
    return v.value;
}

// Fire a broadcast and step the runtime until every thread has finished.
function runBroadcast(vm, name, maxSteps = 100000) {
    vm.runtime.startHats('event_whenbroadcastreceived', {BROADCAST_OPTION: name});
    let steps = 0;
    while (vm.runtime.threads.length > 0 && steps < maxSteps) {
        vm.runtime._step();
        steps++;
    }
    if (vm.runtime.threads.length > 0) throw new Error('threads still running after ' + steps + ' steps');
    return steps;
}

module.exports = {loadVM, getVar, setVar, getList, runBroadcast, stage};
