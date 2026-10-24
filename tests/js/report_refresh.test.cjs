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

function loadReports() {
    const document = eventTarget();
    // a report is on screen: #report-content is in the page (guards against
    // redrawing a closed report)
    document.getElementById = (id) => (id === 'report-content' ? {} : null);
    const win = eventTarget();
    const ctx = {
        console, JSON, Promise, setTimeout, clearTimeout,
        CustomEvent, document, window: win,
        API: { get: async () => ({ value: null }) },
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false },
    };
    vm.createContext(ctx);
    vm.runInContext(SRC('reports.js') + '\nthis.ReportsPage = ReportsPage;', ctx);
    const RP = ctx.ReportsPage;
    let renders = 0;
    RP._live = { render: () => { renders += 1; } };
    RP._autoRefresh = true;
    const wait = () => new Promise((r) => setTimeout(r, 360));  // past the 300ms debounce
    const write = (p) => document.dispatchEvent(new CustomEvent('slowbooks:write', { detail: { method: 'POST', path: p } }));
    const focus = () => win.dispatchEvent({ type: 'focus' });
    return { RP, renders: () => renders, wait, write, focus };
}

test('a burst of two writes redraws the live report once', async () => {
    const t = loadReports();
    t.write('/invoices');
    t.write('/journal');
    await t.wait();
    assert.equal(t.renders(), 1);
});

test('the preference off means no automatic redraw', async () => {
    const t = loadReports();
    t.RP._autoRefresh = false;
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

test('regaining the window focus redraws the live report', async () => {
    const t = loadReports();
    t.focus();
    await t.wait();
    assert.equal(t.renders(), 1);
});

test('a closed report (no #report-content) is not redrawn in the background', async () => {
    const t = loadReports();
    t.RP._reportOnScreen = () => false;  // the dialog was closed, _live still set
    t.write('/invoices');
    await t.wait();
    assert.equal(t.renders(), 0);
});
