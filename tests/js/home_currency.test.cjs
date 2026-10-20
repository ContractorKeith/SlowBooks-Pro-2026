// The screen prints the company's home currency (#125, discussion #112):
// formatCurrency read "en-US / USD" and nothing else, so a company whose
// books are in rand saw "$" on every page. It now reads App.settings.
// home_currency and prints as app/services/money.py does on the server:
// the symbol for the home currency, the ISO code for any other, and for a
// home currency with no symbol in the table.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load(home) {
    const context = { window: {}, App: { settings: { home_currency: home } }, document: { addEventListener() {} } };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    vm.runInContext(js + '\nthis.formatCurrency = formatCurrency; this.currencyOptionsHtml = currencyOptionsHtml; this.homeCurrency = homeCurrency;', context);
    return context;
}

test('a rand company reads rand', () => {
    const { formatCurrency } = load('ZAR');
    assert.equal(formatCurrency(1234.5), 'R1,234.50');
    assert.equal(formatCurrency(-10), '-R10.00');
    assert.equal(formatCurrency(0), 'R0.00');
    assert.equal(formatCurrency(-0.001), 'R0.00');
    assert.equal(formatCurrency(null), 'R0.00');
    // a dollar document is the foreign one; the home code is not foreign
    assert.equal(formatCurrency(850, 'USD'), 'USD 850.00');
    assert.equal(formatCurrency(-10, 'EUR'), 'EUR -10.00');
    assert.equal(formatCurrency(850, 'zar'), 'R850.00');
    // a unit price keeps its places
    assert.equal(formatCurrency('0.045', undefined, 4), 'R0.045');
});

test('a currency with no symbol leads with its code', () => {
    const { formatCurrency } = load('BWP');
    assert.equal(formatCurrency(1234.5), 'BWP 1,234.50');
    assert.equal(formatCurrency(-10), 'BWP -10.00');
});

test('no setting, or a blank one, is dollars as before', () => {
    assert.equal(load(undefined).formatCurrency(1234.5), '$1,234.50');
    assert.equal(load(' ').formatCurrency(-10), '-$10.00');
    assert.equal(load('eur').homeCurrency(), 'EUR');
});

test('the picker lists every ISO currency, the home one first', () => {
    const { currencyOptionsHtml } = load('ZAR');
    const html = currencyOptionsHtml();
    assert.match(html, /^<option value="ZAR" selected>ZAR — South African Rand<\/option>/);
    assert.ok(html.includes('<option value="USD" >USD — US Dollar</option>'));
    assert.ok(html.includes('<option value="EUR" >EUR — Euro</option>'));
    assert.ok((html.match(/<option /g) || []).length > 150);
    assert.ok(currencyOptionsHtml('EUR').includes('<option value="EUR" selected>'));
});
