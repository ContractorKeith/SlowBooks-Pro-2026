// #550: the shared CostTypes helper renders a per-line cost type cell under
// the same gate as the cost code column (CostCodes.any()), and the Trade
// Packages settings block is hidden outside construction mode through the
// real App.applyTerminology [data-construction] path.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function utilsContext() {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.CostCodes = CostCodes; this.CostTypes = CostTypes;',
        context,
    );
    return context;
}

test('CostTypes renders nothing when the company has no cost codes', () => {
    const ctx = utilsContext();
    ctx.CostCodes._list = [];
    ctx.CostTypes._list = [{ code: 'labor', name: 'Labor' }];
    assert.equal(ctx.CostTypes.any(), false);
    assert.equal(ctx.CostTypes.headHtml(), '');
    assert.equal(ctx.CostTypes.cellHtml('line-cost-type'), '');
});

test('CostTypes renders a header and a line cell when cost codes exist', () => {
    const ctx = utilsContext();
    ctx.CostCodes._list = [{ id: 1, code: '03', name: 'Concrete', label: '03 Concrete' }];
    ctx.CostTypes._list = [
        { code: 'labor', name: 'Labor' },
        { code: 'material', name: 'Material' },
    ];
    assert.equal(ctx.CostTypes.any(), true);
    assert.match(ctx.CostTypes.headHtml(), /<th scope="col">Cost Type<\/th>/);
    const cell = ctx.CostTypes.cellHtml('line-cost-type', 'material');
    assert.match(cell, /<select class="line-cost-type">/);
    assert.match(cell, /<option value="">--<\/option>/); // blank = use the code's type
    assert.match(cell, /<option value="material" selected>Material<\/option>/);
    assert.match(cell, /<option value="labor" >Labor<\/option>/);
});

function appContext(construction) {
    const fakeBlock = { matches: () => false, hidden: undefined, dataset: {} };
    const context = {
        console, setTimeout, clearTimeout, Promise,
        window: {},
        document: { addEventListener() {}, querySelectorAll: () => [] },
        location: { hash: '#/' },
        history: { pushState() {}, replaceState() {} },
        $: () => null,
        $$: (sel) => (sel === '[data-construction]' ? [fakeBlock] : []),
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => construction },
    };
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    for (const name of new Set(src.match(/\b[A-Z][A-Za-z]*Page\b/g) || [])) {
        context[name] = new Proxy({}, { get: () => () => '' });
    }
    vm.createContext(context);
    vm.runInContext(src + '\nthis.App = App;', context);
    return { App: context.App, fakeBlock };
}

test('a [data-construction] block (Trade Packages) is hidden for a business', () => {
    const { App, fakeBlock } = appContext(false);
    App.applyTerminology();
    assert.equal(fakeBlock.hidden, true);
});

test('a [data-construction] block (Trade Packages) shows for a contractor', () => {
    const { App, fakeBlock } = appContext(true);
    App.applyTerminology();
    assert.equal(fakeBlock.hidden, false);
});
