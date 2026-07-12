/**
 * Desktop (pywebview) shim — only active inside the native desktop window.
 *
 * Problem this solves: the app opens print previews, PDFs, CSV exports and
 * attachments with window.open()/target="_blank". In a browser install the
 * new tab shares the session cookie and everything works. In the desktop
 * shell, pywebview routes "new window" requests to the SYSTEM browser,
 * which has a separate cookie jar — so those URLs came back
 * {"detail":"Not authenticated"}.
 *
 * Two things were tried and rejected before this design:
 *   1. An <iframe> overlay — blocked outright by this app's own
 *      Content-Security-Policy: frame-ancestors 'none' ("This content is
 *      blocked. Contact the site owner to fix the issue.").
 *   2. Handing the raw URL to Python to open in a second native pywebview
 *      window — field-tested and still came back "Not authenticated".
 *      pywebview windows in one process nominally share a WebView2
 *      profile, but a fresh top-level browsing context evidently doesn't
 *      reliably carry the first window's session cookie in practice.
 *
 * What actually works: fetch the URL from THIS page's own JavaScript —
 * exactly like the app's normal API calls, which is why those always
 * succeed — then hand the already-fetched content over to Python to
 * display. No second authenticated network request is ever made.
 *   - A response with Content-Disposition: attachment (CSV exports,
 *     attachment downloads) is saved via the same createObjectURL + <a
 *     download> trick the Settings → Backups "Download" link already
 *     uses successfully — entirely in this page, no native window
 *     involved.
 *   - An HTML response (print-preview pages) is handed to
 *     open_document_html(), which opens it in a new native window via
 *     pywebview's html= parameter.
 *   - A PDF response is base64-encoded and handed to open_document_pdf(),
 *     which writes it to a local temp file and opens that (file:// needs
 *     no auth at all) so Chromium's built-in PDF viewer can render it.
 *
 * In a normal browser this file is a no-op.
 */
(function () {
    'use strict';

    // pywebview injects window.pywebview once the window is ready; by the
    // time a user can click anything it's present, so no readiness gating
    // is needed at call time -- only checked defensively below.
    function inDesktopShell() {
        return typeof window.pywebview !== 'undefined'
            || navigator.userAgent.includes('WebView2');
    }

    function isSameOrigin(url) {
        try {
            const u = new URL(url, window.location.href);
            return u.origin === window.location.origin;
        } catch (e) {
            return false;
        }
    }

    function filenameFromDisposition(disposition, fallback) {
        if (!disposition) return fallback;
        const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
        return match ? decodeURIComponent(match[1]) : fallback;
    }

    function arrayBufferToBase64(buffer) {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        const chunkSize = 0x8000; // avoid call-stack limits on String.fromCharCode
        for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
        }
        return btoa(binary);
    }

    function saveBlob(blob, filename) {
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 30000);
    }

    async function openSameOriginUrl(url) {
        let response;
        try {
            response = await fetch(url, { credentials: 'same-origin' });
        } catch (e) {
            if (typeof toast === 'function') toast('Could not load the document: ' + e.message, 'error');
            return;
        }
        if (!response.ok) {
            let detail = '';
            try { detail = (await response.json()).detail || ''; } catch (e) { /* not JSON */ }
            if (typeof toast === 'function') {
                toast(detail || ('Could not load the document (HTTP ' + response.status + ')'), 'error');
            }
            return;
        }

        const disposition = response.headers.get('Content-Disposition') || '';
        const contentType = (response.headers.get('Content-Type') || '').toLowerCase();
        const fallbackName = url.split('/').pop().split('?')[0] || 'download';

        if (/attachment/i.test(disposition)) {
            const blob = await response.blob();
            saveBlob(blob, filenameFromDisposition(disposition, fallbackName));
            return;
        }

        if (contentType.includes('pdf')) {
            const buffer = await response.arrayBuffer();
            const base64 = arrayBufferToBase64(buffer);
            const title = filenameFromDisposition(disposition, fallbackName);
            if (window.pywebview && window.pywebview.api && window.pywebview.api.open_document_pdf) {
                await window.pywebview.api.open_document_pdf(title, base64);
            }
            return;
        }

        // Anything else (print-preview HTML, etc.)
        const html = await response.text();
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_document_html) {
            await window.pywebview.api.open_document_html('SlowBooks Pro 2026', html);
        }
    }

    const realOpen = window.open.bind(window);
    window.open = function (url, target, features) {
        if (url && inDesktopShell() && isSameOrigin(url)) {
            openSameOriginUrl(url);
            return null;
        }
        return realOpen(url, target, features);
    };

    // Same treatment for <a target="_blank"> anchors (attachment downloads,
    // employee documents, print-preview links). Plain download links
    // (no target="_blank") are left alone -- WebView2's native download
    // flow already handles those correctly in the same window.
    document.addEventListener('click', function (e) {
        if (!inDesktopShell()) return;
        const a = e.target && e.target.closest ? e.target.closest('a[target="_blank"]') : null;
        if (!a || !a.href || !isSameOrigin(a.href)) return;
        e.preventDefault();
        openSameOriginUrl(a.href);
    }, true);
})();
