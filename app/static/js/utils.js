/**
 * A nod to QBW32.EXE!CQBFormatUtils  imagined offset: 0x0008C200
 * Original formatting used Win32 GetCurrencyFormat() / GetDateFormat()
 * with the system locale. The BCD-to-string conversion in the original
 * had a special case for negative values that printed parentheses instead
 * of a minus sign — classic accountant move.
 */

function $(sel, parent = document) { return parent.querySelector(sel); }
function $$(sel, parent = document) { return [...parent.querySelectorAll(sel)]; }

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount || 0);
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = dateStr.includes('T')
        ? new Date(dateStr)
        : new Date(dateStr + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return 'Invalid date';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function todayISO() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function toast(message, type = 'success') {
    const container = $('#toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function openModal(title, html) {
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    $('#modal-overlay').classList.remove('hidden');
}

function closeModal() {
    $('#modal-overlay').classList.add('hidden');
}

function statusBadge(status) {
    return `<span class="badge badge-${status}">${status}</span>`;
}

function escapeHtml(str) {
    str = String(str ?? '');
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function disableSubmitButtons() {
    document.querySelectorAll('#modal .btn-primary').forEach(b => { b.disabled = true; b.dataset.origText = b.textContent; b.textContent = 'Saving...'; });
}
function enableSubmitButtons() {
    document.querySelectorAll('#modal .btn-primary').forEach(b => { b.disabled = false; if(b.dataset.origText) b.textContent = b.dataset.origText; });
}

function closeSearchDropdown() {
    const dd = $('#search-results');
    if (dd) dd.classList.add('hidden');
    const input = $('#global-search');
    if (input) input.value = '';
}

/**
 * Shared scaffolding for the document list pages (invoices, bills,
 * estimates, purchase orders, credit memos). Each page previously built
 * the same page-header / status-filter toolbar / empty-state / table
 * skeleton by hand; only the title, buttons, columns, and row markup
 * actually differ, so those stay page-owned.
 *
 *   title:      page heading text
 *   headerHtml: raw HTML rendered next to the heading (action buttons)
 *   filter:     optional {id, rowSelector, options: [[value, label], ...]}
 *               status dropdown; filtering is client-side via filterRows()
 *   empty:      raw HTML rendered inside .empty-state when items is empty
 *   columns:    array of header labels; use {label, cls} for styled columns
 *   items:      the fetched rows
 *   row:        item => '<tr ...>...</tr>' (page keeps escaping/actions)
 */
function renderListPage({ title, headerHtml = '', filter = null, empty, columns, items, row }) {
    let html = `
        <div class="page-header">
            <h2>${title}</h2>
            ${headerHtml}
        </div>`;
    if (filter) {
        const opts = filter.options
            .map(([value, label]) => `<option value="${value}">${label}</option>`)
            .join('');
        html += `
            <div class="toolbar">
                <select id="${filter.id}" onchange="filterRows('${filter.id}', '${filter.rowSelector}')">
                    <option value="">All Statuses</option>
                    ${opts}
                </select>
            </div>`;
    }
    if (items.length === 0) {
        return html + `<div class="empty-state">${empty}</div>`;
    }
    const ths = columns
        .map(c => (typeof c === 'string' ? `<th>${c}</th>` : `<th class="${c.cls}">${c.label}</th>`))
        .join('');
    return html + `<div class="table-container"><table>
        <thead><tr>${ths}</tr></thead><tbody>${items.map(row).join('')}</tbody></table></div>`;
}

function filterRows(selectId, rowSelector) {
    const status = $(`#${selectId}`)?.value;
    $$(rowSelector).forEach(row => {
        row.style.display = (!status || row.dataset.status === status) ? '' : 'none';
    });
}
const COUNTRIES = [
    { code: 'US', name: 'United States' },
    { code: 'CA', name: 'Canada' },
    { code: 'IE', name: 'Ireland' },
    { code: 'GB', name: 'United Kingdom' },
    { code: 'AU', name: 'Australia' },
    { code: '-', name: '──────────', disabled: true },
    { code: 'AR', name: 'Argentina' },
    { code: 'AT', name: 'Austria' },
    { code: 'BE', name: 'Belgium' },
    { code: 'BR', name: 'Brazil' },
    { code: 'BG', name: 'Bulgaria' },
    { code: 'CL', name: 'Chile' },
    { code: 'CN', name: 'China' },
    { code: 'CO', name: 'Colombia' },
    { code: 'HR', name: 'Croatia' },
    { code: 'CZ', name: 'Czech Republic' },
    { code: 'DK', name: 'Denmark' },
    { code: 'EG', name: 'Egypt' },
    { code: 'EE', name: 'Estonia' },
    { code: 'FI', name: 'Finland' },
    { code: 'FR', name: 'France' },
    { code: 'DE', name: 'Germany' },
    { code: 'GR', name: 'Greece' },
    { code: 'HK', name: 'Hong Kong' },
    { code: 'HU', name: 'Hungary' },
    { code: 'IS', name: 'Iceland' },
    { code: 'IN', name: 'India' },
    { code: 'ID', name: 'Indonesia' },
    { code: 'IL', name: 'Israel' },
    { code: 'IT', name: 'Italy' },
    { code: 'JP', name: 'Japan' },
    { code: 'KE', name: 'Kenya' },
    { code: 'LV', name: 'Latvia' },
    { code: 'LT', name: 'Lithuania' },
    { code: 'LU', name: 'Luxembourg' },
    { code: 'MY', name: 'Malaysia' },
    { code: 'MX', name: 'Mexico' },
    { code: 'MA', name: 'Morocco' },
    { code: 'NL', name: 'Netherlands' },
    { code: 'NZ', name: 'New Zealand' },
    { code: 'NG', name: 'Nigeria' },
    { code: 'NO', name: 'Norway' },
    { code: 'PK', name: 'Pakistan' },
    { code: 'PE', name: 'Peru' },
    { code: 'PH', name: 'Philippines' },
    { code: 'PL', name: 'Poland' },
    { code: 'PT', name: 'Portugal' },
    { code: 'RO', name: 'Romania' },
    { code: 'SA', name: 'Saudi Arabia' },
    { code: 'SG', name: 'Singapore' },
    { code: 'SK', name: 'Slovakia' },
    { code: 'SI', name: 'Slovenia' },
    { code: 'ZA', name: 'South Africa' },
    { code: 'KR', name: 'South Korea' },
    { code: 'ES', name: 'Spain' },
    { code: 'SE', name: 'Sweden' },
    { code: 'CH', name: 'Switzerland' },
    { code: 'TW', name: 'Taiwan' },
    { code: 'TH', name: 'Thailand' },
    { code: 'TR', name: 'Turkey' },
    { code: 'UA', name: 'Ukraine' },
    { code: 'AE', name: 'United Arab Emirates' },
    { code: 'UY', name: 'Uruguay' },
    { code: 'VN', name: 'Vietnam' },
];

function countryOptions(selected) {
    return COUNTRIES.map(c =>
        `<option value="${c.code}"${c.disabled ? ' disabled' : ''}${c.code === selected ? ' selected' : ''}>${c.name}</option>`
    ).join('');
}
