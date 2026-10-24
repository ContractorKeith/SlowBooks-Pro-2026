// Reports redraw after an edit, and the Refresh control (#482).
//
// Two standalone loads with stubs:
//   api.js    — API.request fires a `slowbooks:write` event after an ok
//               write, and only then (not on a GET, not on a failed write).
//   reports.js — the listener it installs refreshes a live report once for a
//               burst of writes, not when the preference is off, not for a
//               path that changes no figures, and on window focus.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const SRC = (name) => fs.readFileSync(path.join(__dirname, '../../app/static/js', name), 'utf8');

// A tiny event target and CustomEvent, enough for document/window here.
function eventTarget() {
    const listeners = {};
    return {
        addEventListener(type, cb) { (listeners[type] = listeners[type] || []).push(cb); },
        dispatchEvent(ev) { (listeners[ev.type] || []).forEach((cb) => cb(ev)); return true; },
    };
}
class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = init && init.detail; }
}

function loadApi() {
    const document = eventTarget();
    const fired = [];
    document.addEventListener('slowbooks:write', (e) => fired.push(e.detail));
    let next = null;  // the response the next fetch returns
    const ctx = {
        console, JSON, Promise, URLSearchParams, setTimeout,
        CustomEvent, document,
        window: {},
        localStorage: { getItem: () => null },
        fetch: async () => next,
    };
    vm.createContext(ctx);
    vm.runInContext(SRC('api.js') + '\nthis.API = API;', ctx);
    const respond = (status, body = {}) => {
        next = {
            status,
            ok: status >= 200 && status < 300,
            statusText: '',
            headers: { get: () => null },
            json: async () => body,
        };
    };
    return { API: ctx.API, fired, respond };
}

test('an ok write fires one slowbooks:write with its method and path', async () => {
    const { API, fired, respond } = loadApi();
    respond(200, { id: 1 });
    await API.request('POST', '/invoices', { total: 5 });
    // the detail is made inside the vm realm, so read its fields rather than
    // deep-equal across realms
    assert.equal(fired.length, 1);
    assert.equal(fired[0].method, 'POST');
    assert.equal(fired[0].path, '/invoices');
});

test('a GET fires nothing', async () => {
    const { API, fired, respond } = loadApi();
    respond(200, []);
    await API.request('GET', '/invoices');
    assert.deepEqual(fired, []);
});

test('a failed write fires nothing', async () => {
    const { API, fired, respond } = loadApi();
    respond(500, { detail: 'boom' });
    await assert.rejects(() => API.request('POST', '/invoices', { total: 5 }));
    assert.deepEqual(fired, []);
});

function loadReports(prefValue) {
    const document = eventTarget();
    // Model a real open report dialog the way closeModal leaves it: the
    // overlay is shown (closeModal hides it, it does not empty the body) and
    // #report-content lives inside #modal-body.
    const content = {};
    const body = { contains: (n) => n === content };
    const modal = { scrollTop: 0 };
    const overlay = { _hidden: false, classList: { contains: (c) => c === 'hidden' && overlay._hidden } };
    const els = { 'modal-overlay': overlay, 'modal-body': body, 'report-content': content, modal };
    document.getElementById = (id) => (id in els ? els[id] : null);
    document.visibilityState = 'visible';
    const win = eventTarget();
    const ctx = {
        console, JSON, Promise, setTimeout, clearTimeout, Date,
        CustomEvent, document, window: win,
        API: { get: async () => ({ value: prefValue === undefined ? null : prefValue }) },
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false },
    };
    vm.createContext(ctx);
    vm.runInContext(SRC('reports.js') + '\nthis.ReportsPage = ReportsPage;', ctx);
    const RP = ctx.ReportsPage;
    let renders = 0;
    RP._live = { render: () => { renders += 1; } };
    RP._autoRefresh = true;
    const wait = () => new Promise((r) => setTimeout(r, 380));  // past the 300ms debounce
    const write = (p) => document.dispatchEvent(new CustomEvent('slowbooks:write', { detail: { method: 'POST', path: p } }));
    const visible = () => document.dispatchEvent({ type: 'visibilitychange' });
    const close = () => { overlay._hidden = true; };  // closeModal hides the overlay
    return { RP, renders: () => renders, wait, write, visible, close };
}

test('a burst of two writes redraws the live report once', async () => {
    const t = loadReports();
    t.write('/invoices');
    t.write('/journal');
    await t.wait();
    assert.equal(t.renders(), 1);
});

test('the preference off (set) means no automatic redraw', async () => {
    const t = loadReports();
    t.RP._autoRefresh = false;
    t.RP._autoRefreshLoaded = true;
    t.write('/invoices');
    await t.wait();
    assert.equal(t.renders(), 0);
});

test('a stored {enabled:false} preference is honoured on the first write', async () => {
    // the pref has not been read yet; the debounced callback awaits the read
    // and sees false before redrawing
    const t = loadReports({ enabled: false });
    t.write('/invoices');
    await t.wait();
    assert.equal(t.renders(), 0);
});

test('a write that changes no figures is ignored', async () => {
    const t = loadReports();
    t.write('/preferences/report_auto_refresh');
    t.write('/saved-reports');
    await t.wait();
    assert.equal(t.renders(), 0);
});

test('no live report means no redraw', async () => {
    const t = loadReports();
    t.RP._live = null;
    t.write('/invoices');
    await t.wait();
    assert.equal(t.renders(), 0);
});

test('the tab becoming visible again redraws the live report', async () => {
    const t = loadReports();
    t.visible();
    await t.wait();
    assert.equal(t.renders(), 1);
});

test('a closed report is not redrawn in the background', async () => {
    // closeModal hides the overlay but leaves _live set and the body in place
    const t = loadReports();
    t.close();
    t.write('/invoices');
    await t.wait();
    assert.equal(t.renders(), 0);
});
