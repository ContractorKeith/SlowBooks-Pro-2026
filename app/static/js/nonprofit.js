/**
 * Nonprofit documents — what a fund-accounting ledger posts that a
 * business never does. Release from Restriction: a restricted fund spent
 * money for its purpose, so that much moves from net assets with donor
 * restrictions to net assets without. The form suggests the fund's
 * unreleased spending for the period; the posting is DR 3400 / CR 3300
 * tagged to the fund, voidable like any other document.
 */
const ReleasesPage = {
    _funds: [],

    async render() {
        const releases = await API.get('/nonprofit/releases');
        const rows = releases.map(r => `<tr class="clickable" onclick="ReleasesPage.view(${r.id})" style="${r.status === 'void' ? 'opacity:.6' : ''}">
            <td>${escapeHtml(r.number)}</td>
            <td>${escapeHtml(r.date)}</td>
            <td>${escapeHtml(r.class_name || '')}</td>
            <td>${escapeHtml(r.period_start || '')} — ${escapeHtml(r.period_end || '')}</td>
            <td>${escapeHtml(r.memo || '')}</td>
            <td class="amount">${formatCurrency(r.amount)}</td>
            <td>${r.status === 'void' ? '<span style="color:#a4242b">void</span>' : 'posted'}</td>
        </tr>`).join('');
        return `
            <div class="page-header">
                <h2>Releases from Restriction</h2>
                <button class="btn btn-primary" onclick="ReleasesPage.showForm()">+ Release</button>
            </div>
            <div class="toolbar" style="font-size:11px; color:var(--gray-500);">
                When a restricted fund spends for its purpose, release that much to net assets without donor restrictions. The amount suggested is the fund's spending in the period less what was already released.
            </div>
            ${releases.length === 0 ? `<div class="empty-state"><p>No releases yet.</p></div>` : `
            <div class="table-container"><table>
                <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">${T('Class')}</th><th scope="col">Period</th><th scope="col">Memo</th><th scope="col" class="amount">Amount</th><th scope="col">Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`}`;
    },

    async showForm() {
        const classes = await API.get('/classes');
        ReleasesPage._funds = classes.filter(c => c.restriction && c.restriction !== 'unrestricted');
        if (!ReleasesPage._funds.length) {
            toast(`No restricted ${T('classes')} yet — mark a ${T('class')} as restricted in Settings`, 'error');
            return;
        }
        const fundOpts = ReleasesPage._funds.map(f => `<option value="${f.id}">${escapeHtml(f.name)}</option>`).join('');
        const year = new Date().getFullYear();
        openModal('Release from Restriction', `
            <form onsubmit="ReleasesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Class')} *</label>
                        <select name="class_id" required onchange="ReleasesPage.suggest()">${fundOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Period start</label>
                        <input name="period_start" type="date" value="${year}-01-01" onchange="ReleasesPage.suggest()"></div>
                    <div class="form-group"><label>Period end</label>
                        <input name="period_end" type="date" value="${todayISO()}" onchange="ReleasesPage.suggest()"></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group" style="display:flex;align-items:flex-end;">
                        <button type="button" class="btn btn-secondary" onclick="ReleasesPage.suggest()">Suggest</button></div>
                    <div class="form-group full-width" id="release-hint" style="font-size:11px; color:var(--gray-500);"></div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="memo" placeholder="e.g. June youth program spending"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Release</button>
                </div>
            </form>`);
        ReleasesPage.suggest();
    },

    async suggest() {
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const hint = $('#release-hint');
        try {
            const s = await API.get(`/nonprofit/releases/suggest?class_id=${form.class_id.value}&start_date=${form.period_start.value}&end_date=${form.period_end.value}`);
            form.amount.value = Number(s.suggested).toFixed(2);
            if (hint) hint.textContent = `${s.class_name}: spent ${formatCurrency(s.expenses)} in the period, ${formatCurrency(s.released)} already released, ${formatCurrency(s.suggested)} to release.`;
        } catch (err) {
            if (hint) hint.textContent = err.message;
        }
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        try {
            const rel = await API.post('/nonprofit/releases', {
                date: form.date.value,
                class_id: parseInt(form.class_id.value),
                amount: parseFloat(form.amount.value),
                period_start: form.period_start.value || null,
                period_end: form.period_end.value || null,
                memo: form.memo.value || null,
            });
            toast(`${rel.number}: ${formatCurrency(rel.amount)} released from ${rel.class_name}`);
            closeModal();
            App.navigate('#/releases');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        let r;
        try { r = await API.get(`/nonprofit/releases/${id}`); } catch (err) { toast(err.message, 'error'); return; }
        openModal(`Release ${r.number}`, `
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <div style="font-size:13px">
                    <div><strong>${escapeHtml(r.date)}</strong> · ${escapeHtml(r.class_name || '')}</div>
                    <div style="color:#666">Period ${escapeHtml(r.period_start || '')} — ${escapeHtml(r.period_end || '')}</div>
                    ${r.memo ? `<div style="color:#666">${escapeHtml(r.memo)}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div style="font-size:20px;font-weight:700">${formatCurrency(r.amount)}</div>
                    <div>${r.status === 'void' ? '<span style="color:#a4242b;font-weight:600">VOID</span>' : `<button class="btn btn-sm btn-secondary" onclick="ReleasesPage.voidEntry(${r.id})">Void</button>`}</div>
                </div>
            </div>
            <div style="font-size:11px;color:var(--gray-500)">Posted as a debit to Net Assets With Donor Restrictions and a credit to Net Assets Without, both tagged to the ${T('class')}.</div>
            <div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
    },

    async voidEntry(id) {
        if (!confirm('Void this release? A reversing entry is posted; the original stays in the ledger.')) return;
        try {
            await API.post(`/nonprofit/releases/${id}/void`, {});
            toast('Release voided');
            closeModal();
            App.navigate('#/releases');
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.ReleasesPage = ReleasesPage;
