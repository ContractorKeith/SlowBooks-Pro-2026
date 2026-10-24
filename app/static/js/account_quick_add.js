/**
 * Add a chart account beside an account picker, without leaving the form
 * (#485, and the register's #386 before it). The register's category picker
 * grew an inline "new account" panel; the same panel now sits beside the
 * Account cell on a bill, expense, purchase order and vendor credit line,
 * offered there for expense and cost-of-goods accounts only. Modelled on the
 * register's own BankingPage._newAccountPanel, now a thin wrapper around this.
 *
 * The panel opens from an Add BUTTON beside the select, never from a "+ Add…"
 * option on the select itself: the #386 review found a native select option
 * one ArrowDown from the top trapped the keyboard. Nothing in the panel is
 * `required`, so the host form still submits while the panel is hidden.
 *
 * An onclick in an HTML string cannot carry a closure, so each picker that
 * wants the panel registers its wiring first, keyed by the select's id:
 *
 *   register(selectId, opts)   opts:
 *       types      account types offered, default the register's six
 *       excludeId  an account never offered as a parent (the register's own)
 *       accounts   () => the host's current account list (the parents come
 *                  from it; the host keeps it, so a later line offers a
 *                  just-made account)
 *       onAdded    (made, selectId) => void, after the save: the host pushes
 *                  `made` into its list and into its other Account selects
 *       next       () => the element to focus once the account is made
 *
 *   button(selectId, label='Add')   the Add button's HTML; '' for a sign-in
 *       that cannot save (read-only, accountant), the guard ItemQuickAdd uses
 *   panel(selectId)                 the hidden panel's HTML
 *   open / typeChanged / cancel / save (selectId)   the panel's own handlers
 *
 * Save posts the account, hands it to the host's onAdded, picks it on this
 * select, toasts "<number - >name added to the chart", closes the panel and
 * moves the focus on with next(). Cancel only closes the panel: the picker
 * keeps whatever was already chosen (#386 review — the panel no longer opens
 * from a "__new__" choice, so there is nothing to undo).
 */
const AccountQuickAdd = {
    // The register's six (BankingPage.CATEGORY_TYPES); a purchase line passes
    // ['expense', 'cogs'] instead.
    TYPES: ['expense', 'income', 'cogs', 'asset', 'liability', 'equity'],
    // register(selectId, opts) keeps each picker's wiring here, keyed by the
    // select's id; the entries persist and a re-registration overwrites one.
    _registry: {},

    register(selectId, opts) {
        AccountQuickAdd._registry[selectId] = opts || {};
    },
    _opts(selectId) {
        return AccountQuickAdd._registry[selectId] || {};
    },

    // A sign-in that can save an account. The server is the enforcement; this
    // only keeps a read-only or accountant sign-in from an Add whose save the
    // server would refuse.
    canAdd() {
        return !(typeof App !== 'undefined' && App && typeof App.isLocked === 'function' && App.isLocked());
    },

    // The Add button beside the select. Hidden for a sign-in that cannot
    // save, like ItemQuickAdd's "+ Add item". The accessible name comes from
    // the picker's `ariaLabel` so each line's button reads distinctly (a line
    // table has one per row); the default is "Add a new account".
    button(selectId, label = 'Add') {
        if (!AccountQuickAdd.canAdd()) return '';
        const name = AccountQuickAdd._opts(selectId).ariaLabel || 'Add a new account';
        return `<button type="button" id="${selectId}-add" class="btn btn-sm btn-secondary" aria-label="${escapeHtml(name)}" title="${escapeHtml(name)}" onclick="AccountQuickAdd.open('${selectId}')">${escapeHtml(label)}</button>`;
    },

    _cap(t) {
        return t.charAt(0).toUpperCase() + t.slice(1);
    },

    // The hidden panel: name, type, an optional parent of that type, an
    // optional number. Nothing `required`, so the host form still submits
    // while it is hidden.
    panel(selectId) {
        if (!AccountQuickAdd.canAdd()) return '';
        const id = `${selectId}-new`;
        const types = (AccountQuickAdd._opts(selectId).types || AccountQuickAdd.TYPES)
            .map(t => `<option value="${t}">${AccountQuickAdd._cap(t)}</option>`).join('');
        // A host with more than one panel on a page (the bank-feed review has
        // one per line) passes `labelScope` so no two panels share a field
        // name a screen reader can't tell apart (#492, #198). The scope goes in
        // each field's aria-label, not the visible label text — a review row's
        // category cell is filtered by payee text elsewhere (the Mercury review
        // screen), so the scope must not land in the cell's text. The register
        // and rule form pass nothing and keep the plain labels.
        const scope = AccountQuickAdd._opts(selectId).labelScope;
        const s = scope ? ` (${escapeHtml(scope)})` : '';
        const al = (base) => (s ? ` aria-label="${base}${s}"` : '');
        // Enter inside the panel saves the account, never the host form (a
        // bill, expense or bank rule is a form; Enter on a bare select used to
        // submit it and lose the panel). On the two text boxes Enter saves; on
        // the two selects it is only swallowed, so a choice is not a save.
        const key = `onkeydown="if (event.key === 'Enter') { event.preventDefault(); AccountQuickAdd.save('${selectId}'); }"`;
        const keySel = `onkeydown="if (event.key === 'Enter') { event.preventDefault(); }"`;
        return `<div id="${id}" style="display:none; padding:8px; border:1px solid var(--gray-300); border-radius:4px; margin-top:4px; min-width:220px;">
            <div class="form-grid">
                <div class="form-group"><label for="${id}-name">Account name *</label>
                    <input id="${id}-name" aria-label="New account name${s}" aria-required="true" ${key}></div>
                <div class="form-group"><label for="${id}-type">Type</label>
                    <select id="${id}-type" data-no-search${al('Type')} onchange="AccountQuickAdd.typeChanged('${selectId}')" ${keySel}>${types}</select></div>
                <div class="form-group"><label for="${id}-parent">Sub-account of</label>
                    <select id="${id}-parent" data-no-search${al('Sub-account of')} ${keySel}><option value="">None</option></select></div>
                <div class="form-group"><label for="${id}-number">Number <span style="font-weight:normal; color:var(--gray-500);">(optional)</span></label>
                    <input id="${id}-number" maxlength="20"${al('Number, optional')} placeholder="e.g. 6150, or leave blank" ${key}></div>
            </div>
            <div style="display:flex; gap:8px; margin-top:6px;">
                <button type="button" class="btn btn-sm btn-primary" onclick="AccountQuickAdd.save('${selectId}')">Add account</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="AccountQuickAdd.cancel('${selectId}')">Cancel</button>
            </div>
        </div>`;
    },

    // Opened by the Add button, never by the select: arrowing through the
    // accounts must not spring the panel (#386 review).
    open(selectId) {
        const panel = document.getElementById(`${selectId}-new`);
        if (!panel) return;
        panel.style.display = '';
        AccountQuickAdd.typeChanged(selectId);
        AccountQuickAdd._focus(document.getElementById(`${selectId}-new-name`));
    },

    // The parents offered are the accounts of the chosen type, never the
    // host's excluded account (the register's own), another bank or card, or
    // a control account the software posts to by number (#386 review).
    typeChanged(selectId) {
        const typeEl = document.getElementById(`${selectId}-new-type`);
        const parent = document.getElementById(`${selectId}-new-parent`);
        if (!typeEl || !parent) return;
        const opts = AccountQuickAdd._opts(selectId);
        const type = typeEl.value;
        const list = (typeof opts.accounts === 'function' ? opts.accounts() : null) || [];
        parent.innerHTML = '<option value="">None</option>' + list
            .filter(a => a.account_type === type && a.id !== opts.excludeId && !a.bank_kind && !a.is_control && a.is_active !== false)
            .map(a => `<option value="${a.id}">${accountLabel(a)}</option>`).join('');
    },

    cancel(selectId) {
        const sel = document.getElementById(selectId);
        const panel = document.getElementById(`${selectId}-new`);
        if (panel) panel.style.display = 'none';
        if (sel) AccountQuickAdd._focus(sel._cbx ? sel._cbx.box : sel);
    },

    async save(selectId) {
        const f = (part) => document.getElementById(`${selectId}-new-${part}`);
        const panel = document.getElementById(`${selectId}-new`);
        const name = (f('name').value || '').trim();
        if (!name) { toast('Name the account', 'error'); AccountQuickAdd._focus(f('name')); return null; }
        const opts = AccountQuickAdd._opts(selectId);
        let made;
        try {
            made = await API.post('/accounts', {
                name,
                account_type: f('type').value,
                parent_id: f('parent').value ? parseInt(f('parent').value, 10) : null,
                account_number: f('number').value.trim() || null,
            });
        } catch (err) { toast(err.message, 'error'); return null; }
        // this select gets the new account picked; the host puts it into its
        // own list and its other Account selects (onAdded)
        const sel = document.getElementById(selectId);
        AccountQuickAdd._addOption(sel, made);
        if (typeof opts.onAdded === 'function') opts.onAdded(made, selectId);
        if (sel) sel.value = String(made.id);
        f('name').value = ''; f('number').value = ''; f('parent').value = '';
        if (panel) panel.style.display = 'none';
        toast(`${made.account_number ? made.account_number + ' - ' : ''}${made.name} added to the chart`);
        AccountQuickAdd._focus(typeof opts.next === 'function' ? opts.next() : sel);
        return made;
    },

    // Put a new account's option in the matching <optgroup> when the select
    // has them (PurchaseAccounts.options groups COGS and Expenses, with Other
    // for the rest), else at the end (the register's plain selects).
    _addOption(sel, made) {
        if (!sel || (sel.querySelector && sel.querySelector(`option[value="${made.id}"]`))) return;
        const opt = `<option value="${made.id}">${accountLabel(made)}</option>`;
        const label = { cogs: 'Cost of Goods Sold', expense: 'Expenses' }[made.account_type];
        const groups = sel.querySelectorAll ? [...sel.querySelectorAll('optgroup')] : [];
        const target = (label && groups.find(g => g.label === label))
            || (groups.length && groups.find(g => g.label === 'Other'))
            || null;
        (target || sel).insertAdjacentHTML('beforeend', opt);
    },

    _focus(el) {
        if (!el) return;
        if (typeof focusFor === 'function') focusFor(el, true);
        else if (typeof el.focus === 'function') el.focus();
    },
};
