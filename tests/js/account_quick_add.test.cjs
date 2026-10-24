// The shared Add-account helper (#485): a bill, expense, PO or vendor-credit
// line opens the register's inline new-account panel from an Add button, makes
// an expense or COGS account and picks it, without leaving the form. Loaded
// standalone the way test_discount_item_in_form.py loads a form module, with a
// tiny fake document.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function fakeDoc() {
    const nodes = {};
    let focused = null;
    const make = (id) => {
        const el = {
            id, value: '', innerHTML: '', style: {}, dataset: {}, _opts: [],
            focus() { focused = id; },
            insertAdjacentHTML(_pos, html) {
                this.innerHTML += html;
                const m = html.match(/value="([^"]*)"/);
                if (m) this._opts.push(m[1]);
            },
            querySelector(sel) {
                const m = sel.match(/option\[value="([^"]*)"\]/);
                return m ? (this._opts.includes(m[1]) ? {} : null) : null;
            },
            closest() { return null; },
        };
        nodes[id] = el;
        return el;
    };
    return {
        make,
        get focused() { return focused; },
        document: {
            getElementById: (id) => nodes[id] || null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener: () => {},
        },
    };
}

function load(doc, app) {
    const ctx = {
        console, window: {}, document: doc.document, App: app,
        escapeHtml: (s) => String(s ?? ''),
        accountLabel: (a) => (a.account_number ? a.account_number + ' - ' : '') + (a.name || ''),
        focusFor: (el) => { if (el && el.focus) el.focus(); return true; },
        toast: (...args) => { ctx._toasts.push(args); },
        API: { post: async (p, body) => { ctx._posted.push([p, body]); return ctx._postResult; } },
        _toasts: [], _posted: [], _postResult: null,
    };
    vm.createContext(ctx);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/account_quick_add.js'), 'utf8');
    vm.runInContext(src + '\nthis.AccountQuickAdd = AccountQuickAdd;', ctx);
    return ctx;
}

test('the Add button is hidden for a sign-in that cannot save', () => {
    const doc = fakeDoc();
    const locked = load(doc, { isLocked: () => true });
    assert.equal(locked.AccountQuickAdd.button('x'), '');
    const open = load(doc, { isLocked: () => false });
    const html = open.AccountQuickAdd.button('bill-acct-0', 'Add');
    assert.match(html, /id="bill-acct-0-add"/);
    assert.match(html, /AccountQuickAdd\.open\('bill-acct-0'\)/);
});

test('the parents offered leave out control, bank and the excluded account', () => {
    const doc = fakeDoc();
    const ctx = load(doc, { isLocked: () => false });
    const accounts = [
        { id: 1, name: 'Checking', account_number: '1000', account_type: 'expense', bank_kind: 'bank' },
        { id: 2, name: 'Automobile', account_number: '6100', account_type: 'expense' },
        { id: 3, name: 'A/P Control', account_number: '2000', account_type: 'expense', is_control: true },
        { id: 4, name: 'Office', account_number: '6200', account_type: 'expense' },
        { id: 5, name: 'COGS', account_number: '5000', account_type: 'cogs' },
    ];
    ctx.AccountQuickAdd.register('f-acct', {
        types: ['expense', 'cogs'], excludeId: 4, accounts: () => accounts,
    });
    doc.make('f-acct-new-type').value = 'expense';
    const parent = doc.make('f-acct-new-parent');
    ctx.AccountQuickAdd.typeChanged('f-acct');
    // only 6100 Automobile: the bank, the control account and the excluded
    // 6200 Office are out, and 5000 is a COGS account, not an expense
    assert.match(parent.innerHTML, /6100 - Automobile/);
    assert.doesNotMatch(parent.innerHTML, /Checking|Control|Office|COGS/);
    // the COGS type offers only 5000
    doc.make('f-acct-new-type').value = 'cogs';
    ctx.AccountQuickAdd.typeChanged('f-acct');
    assert.match(parent.innerHTML, /5000 - COGS/);
    assert.doesNotMatch(parent.innerHTML, /Automobile/);
});

test('save posts the account, picks it, and hands it to the host', async () => {
    const doc = fakeDoc();
    const ctx = load(doc, { isLocked: () => false });
    ctx._postResult = { id: 99, name: 'Truck Fuel', account_type: 'expense', account_number: '6150' };
    const added = [];
    const next = doc.make('amount');
    ctx.AccountQuickAdd.register('b-acct', {
        types: ['expense', 'cogs'], accounts: () => [],
        onAdded: (made, fromId) => added.push([made.id, fromId]),
        next: () => next,
    });
    const sel = doc.make('b-acct');
    doc.make('b-acct-new-name').value = 'Truck Fuel';
    doc.make('b-acct-new-type').value = 'expense';
    doc.make('b-acct-new-parent').value = '2';
    doc.make('b-acct-new-number').value = '6150';
    const panel = doc.make('b-acct-new');
    const made = await ctx.AccountQuickAdd.save('b-acct');
    // the body crosses the vm realm, so compare it by value, not prototype
    assert.deepEqual(JSON.parse(JSON.stringify(ctx._posted)), [['/accounts', {
        name: 'Truck Fuel', account_type: 'expense', parent_id: 2, account_number: '6150',
    }]]);
    assert.equal(made.id, 99);
    assert.equal(sel.value, '99');
    assert.ok(sel._opts.includes('99'));
    assert.deepEqual(added, [[99, 'b-acct']]);
    assert.equal(panel.style.display, 'none');
    assert.equal(doc.focused, 'amount');
    assert.match(ctx._toasts[0][0], /6150 - Truck Fuel added to the chart/);
});

test('save refuses a blank name without posting', async () => {
    const doc = fakeDoc();
    const ctx = load(doc, { isLocked: () => false });
    doc.make('n-acct');
    doc.make('n-acct-new-name').value = '   ';
    doc.make('n-acct-new-type').value = 'expense';
    doc.make('n-acct-new-parent').value = '';
    doc.make('n-acct-new-number').value = '';
    ctx.AccountQuickAdd.register('n-acct', { accounts: () => [] });
    const made = await ctx.AccountQuickAdd.save('n-acct');
    assert.equal(made, null);
    assert.deepEqual(ctx._posted, []);
    assert.equal(doc.focused, 'n-acct-new-name');
});

test('cancel keeps the current choice and only closes the panel', () => {
    const doc = fakeDoc();
    const ctx = load(doc, { isLocked: () => false });
    const sel = doc.make('c-acct');
    sel.value = '5';
    const panel = doc.make('c-acct-new');
    panel.style.display = '';
    ctx.AccountQuickAdd.register('c-acct', { accounts: () => [] });
    ctx.AccountQuickAdd.cancel('c-acct');
    assert.equal(sel.value, '5');
    assert.equal(panel.style.display, 'none');
    assert.equal(doc.focused, 'c-acct');
});
