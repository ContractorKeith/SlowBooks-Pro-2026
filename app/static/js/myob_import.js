/**
 * MYOB Import — upload the CSV bundle, dry-run, then import.
 * The Import button only unlocks after a passing dry-run.
 */
const MYOBImportPage = {
    _dryRunOk: false,

    async render() {
        MYOBImportPage._dryRunOk = false;
        return `
            <div class="page-header">
                <h2>MYOB Import</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Migrate accounting history from MYOB AccountRight / MYOB Business exports
                </div>
            </div>
            <div class="card" style="max-width:640px;">
                <p style="font-size:12px; margin-bottom:8px;">
                    Export from MYOB and upload together: the <strong>Accounts
                    list</strong> and <strong>Transaction/General Journals</strong>
                    (required), plus a <strong>Trial Balance</strong> (recommended —
                    enables balance verification). Tab-separated .TXT exports from
                    AccountRight and CSVs from MYOB Business both work; files are
                    recognized by name.
                </p>
                <input type="file" id="myob-files" multiple accept=".csv,.txt" onchange="MYOBImportPage.reset()">
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="MYOBImportPage.dryRun()">Dry Run</button>
                    <button class="btn btn-danger" id="myob-import-btn" disabled onclick="MYOBImportPage.doImport()">Import</button>
                </div>
                <div id="myob-result" style="margin-top:12px; font-size:12px;"></div>
            </div>`;
    },

    reset() {
        MYOBImportPage._dryRunOk = false;
        const btn = $('#myob-import-btn');
        if (btn) btn.disabled = true;
        const out = $('#myob-result');
        if (out) out.innerHTML = '';
    },

    _formData() {
        const files = $('#myob-files').files;
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
        $('#myob-result').innerHTML = `${head}<ul style="margin-top:6px;">${errs}${warns}</ul>`;
    },

    async dryRun() {
        const fd = MYOBImportPage._formData();
        if (!fd) return;
        try {
            const resp = await fetch('/api/myob-import/dry-run', { method: 'POST', body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Dry run failed');
            MYOBImportPage._dryRunOk = data.ok;
            $('#myob-import-btn').disabled = !data.ok;
            MYOBImportPage._renderResult(data, false);
        } catch (err) { toast(err.message, 'error'); }
    },

    async doImport() {
        if (!MYOBImportPage._dryRunOk) { toast('Run a passing dry run first', 'error'); return; }
        const fd = MYOBImportPage._formData();
        if (!fd) return;
        try {
            const resp = await fetch('/api/myob-import/import', { method: 'POST', body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            MYOBImportPage._renderResult(data, true);
            if (data.ok) toast(`Imported ${data.imported_accounts} accounts, ${data.imported_journals} journals`);
        } catch (err) { toast(err.message, 'error'); }
    },
};
