/* Searchable Egyptian city picker for the signup city field.
 *
 * When the selected country is Egypt, the free-text city input is enhanced
 * into a search input plus a <select> fed by the <datalist
 * id="egyptian-cities"> list. Matching mirrors the server normalization
 * (management_system.utils.search.normalize_search_text) and additionally
 * folds ta marbuta (ة -> ه) so common spellings such as "القاهره" also find
 * "القاهرة". Other countries keep the plain text input unchanged.
 */

function normalizeCitySearchTerm(value) {
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

function citySearchMatches(haystack, needle) {
    var normalizedNeedle = normalizeCitySearchTerm(needle);
    if (!normalizedNeedle) return true;
    return normalizeCitySearchTerm(haystack).indexOf(normalizedNeedle) !== -1;
}

function enhanceCitySearch() {
    var countrySelect = document.getElementById("id_country");
    var datalist = document.getElementById("egyptian-cities");
    if (!countrySelect || !datalist) return;

    var cityValues = Array.from(datalist.options)
        .map(function (option) { return option.value; })
        .filter(Boolean);
    /* Labels come from the server-rendered datalist attributes so they always
     * follow the page language (the /jsi18n/ catalog resolves its own). */
    var labels = {
        search: datalist.dataset.searchPlaceholder || "",
        placeholder: datalist.dataset.placeholder || "",
        empty: datalist.dataset.emptyLabel || "",
    };

    function buildSelector() {
        var input = document.getElementById("city");
        if (!input || input.tagName !== "INPUT") return;

        var wrapper = document.createElement("div");
        wrapper.id = "city-search-wrapper";

        var search = document.createElement("input");
        search.type = "search";
        search.id = "city-search";
        search.className = "form-control mb-2";
        search.placeholder = labels.search;
        search.setAttribute("aria-label", labels.search);
        search.setAttribute("autocomplete", "off");

        var select = document.createElement("select");
        select.className = "form-select";
        select.setAttribute("data-city-select", "true");
        select.name = input.name || "city";

        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = labels.placeholder;
        select.appendChild(placeholder);

        cityValues.forEach(function (value) {
            var option = document.createElement("option");
            option.value = value;
            option.textContent = value;
            option.dataset.citySearch = normalizeCitySearchTerm(value);
            select.appendChild(option);
        });

        /* Preserve the in-progress value: exact match, normalized match, or a
         * temporary option so switching countries never drops typed text. */
        var current = (input.value || "").trim();
        if (current) {
            var selected = cityValues.indexOf(current) !== -1 ? current : null;
            if (!selected) {
                var currentNormalized = normalizeCitySearchTerm(current);
                selected = cityValues.find(function (value) {
                    return normalizeCitySearchTerm(value) === currentNormalized;
                }) || null;
            }
            if (selected) {
                select.value = selected;
                input.value = selected;
            } else {
                var temporary = document.createElement("option");
                temporary.value = current;
                temporary.textContent = current;
                temporary.dataset.citySearch = normalizeCitySearchTerm(current);
                select.appendChild(temporary);
                select.value = current;
            }
        }

        var empty = document.createElement("div");
        empty.className = "form-text";
        empty.textContent = labels.empty;
        empty.hidden = true;

        var originalId = input.id;
        input.id = originalId + "-text";
        input.disabled = true;
        input.hidden = true;
        select.id = originalId;

        input.parentNode.insertBefore(wrapper, input.nextSibling);
        wrapper.appendChild(search);
        wrapper.appendChild(select);
        wrapper.appendChild(empty);

        var applyFilter = function () {
            var needle = search.value;
            var visible = 0;
            Array.from(select.options).forEach(function (option) {
                if (!option.value) return;
                var matches = citySearchMatches(option.dataset.citySearch || option.textContent, needle);
                option.hidden = !matches && option.value !== select.value;
                if (matches) visible += 1;
            });
            empty.hidden = !needle || visible > 0;
        };
        search.addEventListener("input", applyFilter);
    }

    function teardownSelector() {
        var select = document.querySelector('[data-city-select="true"]');
        var wrapper = document.getElementById("city-search-wrapper");
        var input = document.getElementById("city-text");
        if (!select || !input) return;
        input.value = select.value;
        input.disabled = false;
        input.hidden = false;
        input.id = "city";
        if (wrapper && wrapper.parentNode) {
            wrapper.parentNode.removeChild(wrapper);
        } else if (select.parentNode) {
            select.parentNode.removeChild(select);
        }
    }

    function applyMode() {
        var isEgypt = (countrySelect.value || "").toUpperCase() === "EG";
        var active = document.querySelector('[data-city-select="true"]');
        if (isEgypt && !active) buildSelector();
        if (!isEgypt && active) teardownSelector();
    }

    if (countrySelect.dataset.citySearchEnhanced !== "true") {
        countrySelect.dataset.citySearchEnhanced = "true";
        countrySelect.addEventListener("change", applyMode);
    }
    applyMode();
}

document.addEventListener("DOMContentLoaded", enhanceCitySearch);
document.addEventListener("htmx:after:swap", enhanceCitySearch);