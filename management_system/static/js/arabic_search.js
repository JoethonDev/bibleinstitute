/* Shared client-side Arabic search normalization for city pickers.
 *
 * Mirrors the server normalization (management_system.utils.search) and
 * additionally folds ta marbuta (ة -> ه) so common spellings such as
 * "القاهره" also find "القاهرة". Used by the signup city picker and the
 * analytics city checklist.
 */

function normalizeArabicSearchTerm(value) {
    var term = String(value || "");
    try {
        term = term.normalize("NFKC");
    } catch (error) {
        /* Engines without String.prototype.normalize keep the literal form. */
    }
    term = term
        .replace(/[\u0623\u0625\u0622\u0671]/g, "\u0627") /* أ إ آ ٱ -> ا */
        .replace(/[\u0649\u0626]/g, "\u064A") /* ى ئ -> ي */
        .replace(/\u0624/g, "\u0648") /* ؤ -> و */
        .replace(/\u0629/g, "\u0647") /* ة -> ه (picker-only fold) */
        .replace(/[\u064B-\u065F\u0670\u0640\u06D6-\u06ED]/g, "") /* harakat/tatweel */
        .toLowerCase();
    return term.replace(/\s+/g, " ").trim();
}

function arabicSearchMatches(haystack, needle) {
    var normalizedNeedle = normalizeArabicSearchTerm(needle);
    if (!normalizedNeedle) return true;
    return normalizeArabicSearchTerm(haystack).indexOf(normalizedNeedle) !== -1;
}