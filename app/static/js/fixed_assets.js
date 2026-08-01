/**
 * Fixed Assets — register, depreciation runs, disposal, CSV import.
 * Designed from the joelmacklow fork's fixed-assets slice.
 */
const FixedAssetsPage = {
    async render() {
        const assets = await API.get('/fixed-assets');
        return renderListPage({
            title: 'Fixed Assets',
            headerHtml: `<div class="btn-group">
                    <button class="btn btn-primary" onclick="FixedAssetsPage.showAssetForm()">+ Register Asset</button>
                    <button class="btn btn-secondary" onclick="FixedAssetsPage.showTypeForm()">+ Asset Type</button>
                    <button class="btn btn-secondary" onclick="FixedAssetsPage.showDepreciationForm()">Run Depreciation</button>
                    <button class="btn btn-secondary" onclick="FixedAssetsPage.showImportForm()">Import CSV</button>
                </div>`,
            empty: `<p>No fixed assets registered yet.</p>
                <button class="btn btn-primary" onclick="FixedAssetsPage.showAssetForm()" style="margin-top:10px;">+ Register your first asset</button>`,
            columns: [
                { label: 'Asset #', key: 'asset_number' },
                { label: 'Name', key: 'name' },
                { label: 'Type', key: 'asset_type_name' },
                { label: 'Purchased', key: 'purchase_date' },
                { label: 'Status', key: 'status' },
                { label: 'Cost', cls: 'amount', key: 'purchase_price' },
                { label: 'Accum. Depr.', cls: 'amount', key: 'accumulated_depreciation' },
                { label: 'Book Value', cls: 'amount', key: 'book_value' },
                'Actions',
            ],
            sort: { id: 'fixed-assets', column: 'asset_number', direction: 'asc' },
            items: assets,
            row: a => `<tr data-status="${a.status}">
                    <td><strong>${escapeHtml(a.asset_number)}</strong></td>
                    <td>${escapeHtml(a.name)}</td>
                    <td>${escapeHtml(a.asset_type_name || '')}</td>
                    <td>${formatDate(a.purchase_date)}</td>
                    <td>${escapeHtml(a.status)}</td>
                    <td class="amount">${formatCurrency(a.purchase_price)}</td>
                    <td class="amount">${formatCurrency(a.accumulated_depreciation)}</td>
                    <td class="amount">${formatCurrency(a.book_value)}</td>
                    <td class="actions">
                        ${a.status === 'registered' ? `<button class="btn btn-sm btn-danger" onclick="FixedAssetsPage.showDisposeForm(${a.id})">Dispose</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    async showTypeForm() {
        const accounts = await API.get('/accounts');
        const opts = kind => accounts
            .filter(a => kind ? a.account_type === kind : true)
            .map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</option>`)
            .join('');
        openModal('New Asset Type', `
            <form onsubmit="FixedAssetsPage.saveType(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required placeholder="e.g. Computer Equipment"></div>
                    <div class="form-group"><label>Depreciation Method</label>
                        <select name="depreciation_method" onchange="FixedAssetsPage.methodChanged(this)">
                            <option value="straight_line" selected>Straight line</option>
                            <option value="declining_balance">Declining balance</option>
                        </select></div>
                    <div class="form-group"><label id="fa-life-label">Effective Life (years)</label>
                        <input name="effective_life_years" type="number" step="0.5" value="5"></div>
                    <div class="form-group"><label>Annual Rate (e.g. 0.20)</label>
                        <input name="annual_rate" type="number" step="0.0001"></div>
                    <div class="form-group"><label>Fixed Asset Account</label>
                        <select name="asset_account_id"><option value="">--</option>${opts('asset')}</select></div>
                    <div class="form-group"><label>Accumulated Depreciation Account</label>
                        <select name="accumulated_depreciation_account_id"><option value="">--</option>${opts('asset')}</select></div>
                    <div class="form-group"><label>Depreciation Expense Account</label>
                        <select name="depreciation_expense_account_id"><option value="">--</option>${opts('expense')}</select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Type</button>
                </div>
            </form>`);
    },

    methodChanged() { /* labels stay visible; both inputs accepted */ },

    async saveType(e) {
        e.preventDefault();
        const form = e.target;
        const num = v => v ? parseFloat(v) : null;
        const intOrNull = v => v ? parseInt(v) : null;
        try {
            await API.post('/fixed-assets/types', {
                name: form.name.value,
                depreciation_method: form.depreciation_method.value,
                effective_life_years: num(form.effective_life_years.value),
                annual_rate: num(form.annual_rate.value),
                asset_account_id: intOrNull(form.asset_account_id.value),
                accumulated_depreciation_account_id: intOrNull(form.accumulated_depreciation_account_id.value),
                depreciation_expense_account_id: intOrNull(form.depreciation_expense_account_id.value),
            });
            toast('Asset type created');
            closeModal();
        } catch (err) { toast(err.message, 'error'); }
    },

    async showAssetForm() {
        const types = await API.get('/fixed-assets/types');
        if (!types.length) { toast('Create an asset type first', 'error'); return; }
        const typeOpts = types.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
        openModal('Register Fixed Asset', `
            <form onsubmit="FixedAssetsPage.saveAsset(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Asset Type *</label>
                        <select name="asset_type_id" required>${typeOpts}</select></div>
                    <div class="form-group"><label>Purchase Date *</label>
                        <input name="purchase_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Purchase Price *</label>
                        <input name="purchase_price" type="number" step="0.01" required></div>
                    <div class="form-group"><label>Salvage Value</label>
                        <input name="salvage_value" type="number" step="0.01" value="0"></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description"></textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Register Asset</button>
                </div>
            </form>`);
    },

    async saveAsset(e) {
        e.preventDefault();
        const form = e.target;
        try {
            await API.post('/fixed-assets', {
                name: form.name.value,
                asset_type_id: parseInt(form.asset_type_id.value),
                purchase_date: form.purchase_date.value,
                purchase_price: parseFloat(form.purchase_price.value),
                salvage_value: parseFloat(form.salvage_value.value) || 0,
                description: form.description.value || null,
            });
            toast('Asset registered');
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    showDepreciationForm() {
        openModal('Run Depreciation', `
            <form onsubmit="FixedAssetsPage.runDepreciation(event)">
                <div class="form-group"><label>Depreciate through *</label>
                    <input name="run_date" type="date" required value="${todayISO()}"></div>
                <div style="font-size:11px; color:var(--text-muted); margin:8px 0;">
                    Posts one journal per asset (DR depreciation expense /
                    CR accumulated depreciation) for the full months elapsed
                    since each asset's last run.
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Run</button>
                </div>
            </form>`);
    },

    async runDepreciation(e) {
        e.preventDefault();
        try {
            const result = await API.post('/fixed-assets/run-depreciation', {
                run_date: e.target.run_date.value,
            });
            toast(`Depreciation posted for ${result.posted} assets (${formatCurrency(result.total)}); ${result.skipped} skipped`);
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showDisposeForm(assetId) {
        const accounts = await API.get('/accounts?account_type=asset');
        const opts = accounts.map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</option>`).join('');
        openModal('Dispose Asset', `
            <form onsubmit="FixedAssetsPage.dispose(event, ${assetId})">
                <div class="form-grid">
                    <div class="form-group"><label>Disposal Date *</label>
                        <input name="disposal_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Proceeds</label>
                        <input name="proceeds" type="number" step="0.01" value="0"></div>
                    <div class="form-group full-width"><label>Deposit Proceeds To *</label>
                        <select name="deposit_account_id" required>${opts}</select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-danger">Dispose</button>
                </div>
            </form>`);
    },

    async dispose(e, assetId) {
        e.preventDefault();
        const form = e.target;
        try {
            const result = await API.post(`/fixed-assets/${assetId}/dispose`, {
                disposal_date: form.disposal_date.value,
                proceeds: parseFloat(form.proceeds.value) || 0,
                deposit_account_id: parseInt(form.deposit_account_id.value),
            });
            const gl = result.gain_loss;
            toast(gl === 0 ? 'Asset disposed' : `Asset disposed (${gl > 0 ? 'gain' : 'loss'} ${formatCurrency(Math.abs(gl))})`);
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    showImportForm() {
        openModal('Import Assets from CSV', `
            <form onsubmit="FixedAssetsPage.importCsv(event)">
                <div class="form-group">
                    <label>CSV file (columns: name, asset_type, purchase_date, purchase_price, salvage_value, description)</label>
                    <input type="file" id="fa-import-file" accept=".csv" required>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Import</button>
                </div>
            </form>`);
    },

    async importCsv(e) {
        e.preventDefault();
        const file = $('#fa-import-file').files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        try {
            const resp = await fetch('/api/fixed-assets/import-csv', { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            const errs = data.errors.length ? ` (${data.errors.length} rows failed)` : '';
            toast(`Imported ${data.imported} assets${errs}`);
            if (data.errors.length) console.warn('Asset import errors:', data.errors);
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },
};
