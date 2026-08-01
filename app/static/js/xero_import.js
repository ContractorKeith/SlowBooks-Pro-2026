/**
 * Xero Import — upload the CSV bundle, dry-run, then import.
 * The Import button only unlocks after a passing dry-run.
 */
const XeroImportPage = {
    _dryRunOk: false,

    async render() {
        XeroImportPage._dryRunOk = false;
        return `
            <div class="page-header">
                <h2>Xero Import</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Migrate accounting history from Xero CSV report exports
                </div>
            </div>
            <div class="card" style="max-width:640px;">
                <p style="font-size:12px; margin-bottom:8px;">
                    Export from Xero as CSV and upload together: <strong>Chart of
                    Accounts</strong> and <strong>General Ledger</strong> (required),
                    <strong>Trial Balance</strong> (recommended — enables balance
                    verification). Files are recognized by name.
                </p>
                <input type="file" id="xero-files" multiple accept=".csv" onchange="XeroImportPage.reset()">
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="XeroImportPage.dryRun()">Dry Run</button>
                    <button class="btn btn-danger" id="xero-import-btn" disabled onclick="XeroImportPage.doImport()">Import</button>
                </div>
                <div id="xero-result" style="margin-top:12px; font-size:12px;"></div>
            </div>`;
    },

    reset() {
        XeroImportPage._dryRunOk = false;
        const btn = $('#xero-import-btn');
        if (btn) btn.disabled = true;
        const out = $('#xero-result');
        if (out) out.innerHTML = '';
    },

    _formData() {
        const files = $('#xero-files').files;
        if (!files.length) { toast('Choose the CSV files first', 'error'); return null; }
        const fd = new FormData();
        for (const f of files) fd.append('files', f);
        return fd;
    },

    _renderResult(data, imported) {
        const errs = data.errors.map(e => `<li style="color:var(--danger);">${escapeHtml(e)}</li>`).join('');
        const warns = data.warnings.map(w => `<li style="color:var(--warning, #a8761f);">${escapeHtml(w)}</li>`).join('');
        const head = imported
            ? (data.ok ? `<strong>Imported ${data.imported_accounts} accounts and ${data.imported_journals} journals.</strong>`
                       : '<strong style="color:var(--danger);">Import refused — fix the dry-run errors below.</strong>')
            : (data.ok ? `<strong>Dry run passed:</strong> ${data.accounts} accounts, ${data.journals} journals ready to import.`
                       : '<strong style="color:var(--danger);">Dry run failed:</strong>');
        $('#xero-result').innerHTML = `${head}<ul style="margin-top:6px;">${errs}${warns}</ul>`;
    },

    async dryRun() {
        const fd = XeroImportPage._formData();
        if (!fd) return;
        try {
            const resp = await fetch('/api/xero-import/dry-run', { method: 'POST', body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Dry run failed');
            XeroImportPage._dryRunOk = data.ok;
            $('#xero-import-btn').disabled = !data.ok;
            XeroImportPage._renderResult(data, false);
        } catch (err) { toast(err.message, 'error'); }
    },

    async doImport() {
        if (!XeroImportPage._dryRunOk) { toast('Run a passing dry run first', 'error'); return; }
        const fd = XeroImportPage._formData();
        if (!fd) return;
        try {
            const resp = await fetch('/api/xero-import/import', { method: 'POST', body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            XeroImportPage._renderResult(data, true);
            if (data.ok) toast(`Imported ${data.imported_accounts} accounts, ${data.imported_journals} journals`);
        } catch (err) { toast(err.message, 'error'); }
    },
};
