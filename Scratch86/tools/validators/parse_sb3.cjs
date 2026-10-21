'use strict';
// Runs the Scratch editor's own project validator (scratch-parser) on an .sb3.
const fs = require('fs');
const parser = require('scratch-parser');
parser(fs.readFileSync(process.argv[2]), false, (err) => {
    if (err) { console.error(String(err), JSON.stringify(err.validationError || '').slice(0, 800)); process.exit(1); }
    console.log('scratch-parser: OK');
});
