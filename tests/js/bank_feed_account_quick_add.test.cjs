// Add a chart account beside the bank-feed review row's category select and
// beside the Bank Rule form's account select (#492), both wired to the shared
// AccountQuickAdd helper. Loaded in a vm with a tiny fake document, the way
// register_check_probe.js loads banking.js. The behaviours checked here are
// the wiring the browser test drives end to end: the review's onAdded puts the
// new account on every review-cat select and sets it on the line (setCategory),
// and the rule form registers rule-account with the six category types.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '../..');

function load() {
    const selects = [];       // every select.review-cat on the fake page
    const byId = {};
    const makeSelect = (id) => {
        const el = {
            id, value: '', dataset: {}, _opts: [''], style: {},
            insertAdjacentHTML(_pos, html) {
                const m = html.match(/value="([^"]*)"/);
                if (m) this._opts.push(m[1]);
            },
            querySelector(sel) {
                const m = sel.match(/option\[value="([^"]*)"\]/);
                return m ? (this._opts.includes(m[1]) ? {} : null) : null;
            },
            focus() {},
        };
        byId[id] = el;
        return el;
    };
    const ctx = {
        console, setTimeout: () => {}, Promise, window: {},
        location: { hash: '#/' },
        escapeHtml: (s) => String(s ?? ''),
        accountLabel: (a) => (a.account_number ? a.account_number + ' - ' : '') + (a.name || ''),
        accountLabelPlain: (a) => a.name || '',
        focusFor: () => true,
        formatDate: (s) => s || '',
        formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
        toast: () => {},
        openModal: () => {},
        closeModal: () => {},
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false },
        App: { isLocked: () => false, navigate: () => {}, refresh: () => {} },
        _patched: [],
        document: {
            getElementById: (id) => byId[id] || null,
            querySelector: () => null,
            querySelectorAll: (sel) => (sel === 'select.review-cat' ? selects : []),
            addEventListener: () => {},
        },
        _selects: selects, _byId: byId, _makeSelect: makeSelect,
    };
    ctx.API = {
        post: async (p, body) => { ctx._posted = [p, body]; return ctx._postResult; },
        request: async (method, p, body) => { ctx._patched.push([method, p, body]); return {}; },
        get: async () => [],
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'app/static/js/account_quick_add.js'), 'utf8') + '\nthis.AccountQuickAdd = AccountQuickAdd;', ctx);
    for (const f of ['banking.js', 'bank_rules.js']) {
        vm.runInContext(fs.readFileSync(path.join(ROOT, `app/static/js/${f}`), 'utf8'), ctx, { filename: f });
    }
    vm.runInContext('this.BankingPage = BankingPage; this.BankRulesPage = BankRulesPage;', ctx);
    return ctx;
}

test('the review row onAdded puts the new account on every review-cat and sets the line', async () => {
    const ctx = load();
    ctx.BankingPage._accounts = [];
    // two review rows' selects
    const sel7 = ctx._makeSelect('cat-7');
    const sel8 = ctx._makeSelect('cat-8');
    ctx._selects.push(sel7, sel8);

    // wire row 7 (its line id 7, the feed's own bank account id 1)
    ctx.BankingPage._wireReviewAccountAdd({ id: 7, payee: 'OFFICE DEPOT', date: '2026-09-04' }, 'cat-7', 1);
    // the button names the line, so 50 rows are not 50 identical "Add"s
    assert.match(ctx.AccountQuickAdd.button('cat-7', '+'), /aria-label="Add a chart account for OFFICE DEPOT/);
    // the panel's hidden fields for the save
    ctx._makeSelect('cat-7-new'); // the panel container (style only)
    const nameEl = ctx._makeSelect('cat-7-new-name'); nameEl.value = 'Bank Charges';
    const typeEl = ctx._makeSelect('cat-7-new-type'); typeEl.value = 'expense';
    const parentEl = ctx._makeSelect('cat-7-new-parent'); parentEl.value = '';
    const numEl = ctx._makeSelect('cat-7-new-number'); numEl.value = '';

    ctx._postResult = { id: 99, name: 'Bank Charges', account_type: 'expense', account_number: null };
    const made = await ctx.AccountQuickAdd.save('cat-7');
    assert.equal(made.id, 99);

    // picked on this row
    assert.equal(sel7.value, '99');
    // appended to every other review-cat select
    assert.ok(sel8._opts.includes('99'), 'the new option reached the other review row');
    // pushed into the cached chart
    assert.equal(ctx.BankingPage._accounts.length, 1);
    // the line was categorised (setCategory -> PATCH of the statement line)
    const patch = ctx._patched.find(p => p[0] === 'PATCH' && p[1] === '/banking/transactions/7');
    assert.ok(patch, 'setCategory PATCHed the line');
    // the body crosses the vm realm, so compare it by value, not prototype
    assert.deepEqual(JSON.parse(JSON.stringify(patch[2])), { category_account_id: 99 });
});

test('the rule form registers rule-account with the select category types', async () => {
    const ctx = load();
    const accounts = [{ id: 2, name: 'Office', account_number: '6200', account_type: 'expense' }];
    ctx.API.get = async () => JSON.parse(JSON.stringify(accounts));
    await ctx.BankRulesPage.showForm();

    // the Add button and panel markup carry the stable id
    const btn = ctx.AccountQuickAdd.button('rule-account');
    assert.match(btn, /id="rule-account-add"/);
    const panel = ctx.AccountQuickAdd.panel('rule-account');
    assert.match(panel, /id="rule-account-new"/);
    // the panel offers exactly the types the select filters to (five, never
    // equity), so an account made here can always be shown and saved
    const types = (panel.match(/<option value="(\w+)">/g) || []).map(m => m.match(/"(\w+)"/)[1]);
    assert.deepEqual(types, ['expense', 'income', 'asset', 'liability', 'cogs']);

    // saving a new account picks it on the rule-account select
    const sel = ctx._makeSelect('rule-account');
    ctx._makeSelect('rule-account-new');
    ctx._makeSelect('rule-account-new-name').value = 'Bank Fees';
    ctx._makeSelect('rule-account-new-type').value = 'expense';
    ctx._makeSelect('rule-account-new-parent').value = '';
    ctx._makeSelect('rule-account-new-number').value = '';
    ctx._postResult = { id: 55, name: 'Bank Fees', account_type: 'expense', account_number: null };
    const made = await ctx.AccountQuickAdd.save('rule-account');
    assert.equal(made.id, 55);
    assert.equal(sel.value, '55');
    assert.ok(sel._opts.includes('55'));
});
