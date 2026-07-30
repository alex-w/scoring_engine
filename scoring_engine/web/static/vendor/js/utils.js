/**
 * Shared JavaScript utilities for the Scoring Engine application
 */

var ScoringEngineUtils = (function() {
    'use strict';

    /**
     * Sanitize string for use in jQuery selectors
     * Escapes special characters that have meaning in CSS selectors
     * @param {string} str - The string to sanitize
     * @returns {string} - Sanitized string safe for use in selectors
     */
    function sanitizeSelector(str) {
        return str.replace(/[!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~]/g, '\\$&');
    }

    /**
     * Animate a numeric counter from previous value to target value.
     * Skips animation if the value hasn't changed.
     * @param {HTMLElement} el - The element to animate
     * @param {number} target - The target number
     * @param {number} duration - Animation duration in ms (default 800)
     */
    function animateCounter(el, target, duration) {
        duration = duration || 800;
        target = parseInt(target) || 0;
        var prev = el._counterPrev;
        if (prev !== undefined && prev === target) return;
        var from = (prev !== undefined) ? prev : 0;
        el._counterPrev = target;
        var startTime = null;
        function step(ts) {
            if (!startTime) startTime = ts;
            var p = Math.min((ts - startTime) / duration, 1);
            el.textContent = Math.floor(from + (target - from) * p).toLocaleString();
            if (p < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    /**
     * Set text content only if the value has changed.
     * @param {HTMLElement} el - The element to update
     * @param {string} text - The new text content
     */
    function setText(el, text) {
        if (el.textContent !== text) el.textContent = text;
    }

    /**
     * Escape a value for safe interpolation into an HTML string.
     * User-controlled data (usernames, team names, ...) is stored verbatim, so
     * anything built as markup in JS must be escaped here at render time.
     * @param {*} value - The value to escape
     * @returns {string} - HTML-escaped string
     */
    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Escape a value for embedding inside a single-quoted JavaScript string
     * literal that itself lives inside an HTML attribute (e.g. an inline
     * onclick handler).  Apply escapeHtml() to the result as well: this handles
     * the JS layer, escapeHtml() handles the HTML layer.
     * @param {*} value - The value to escape
     * @returns {string} - String safe inside a single-quoted JS literal
     */
    function escapeJsString(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'")
            .replace(/\r/g, '\\r')
            .replace(/\n/g, '\\n');
    }

    /**
     * DataTables column renderer that HTML-escapes the display value.
     * DataTables writes cell content with innerHTML, so any column bound to
     * user-controlled data (team names, usernames, ...) must use this.
     * @returns {function} - A DataTables render callback
     */
    function dtEscape() {
        return function(data, type) {
            return type === 'display' ? escapeHtml(data) : data;
        };
    }

    // Public API
    return {
        sanitizeSelector: sanitizeSelector,
        animateCounter: animateCounter,
        setText: setText,
        escapeHtml: escapeHtml,
        escapeJsString: escapeJsString,
        dtEscape: dtEscape
    };
})();
