function enhanceCountrySelects() {
    document.querySelectorAll('[data-country-select="true"]').forEach(function (countrySelect) {
        if (countrySelect.dataset.countryEnhanced === 'true') return;
        countrySelect.dataset.countryEnhanced = 'true';
        const locale = document.documentElement.lang || 'en';
        const search = document.createElement('input');
        search.type = 'search';
        search.className = 'form-control mb-2';
        search.placeholder = countrySelect.dataset.searchPlaceholder || gettext('Search countries');
        search.setAttribute('aria-label', countrySelect.getAttribute('aria-label') || search.placeholder);
        search.setAttribute('autocomplete', 'off');
        search.setAttribute('data-country-search', 'true');
        countrySelect.parentNode.insertBefore(search, countrySelect);

        let displayNames;
        try {
            displayNames = new Intl.DisplayNames([locale], { type: 'region' });
        } catch (error) {
            displayNames = null;
        }
        Array.from(countrySelect.options).forEach(function (option) {
            const code = option.value.toUpperCase();
            const name = code.length === 2 && displayNames ? displayNames.of(code) : option.textContent;
            option.textContent = name ? (code.length === 2 ? `${name} (${code})` : name) : code;
            option.dataset.countrySearch = `${name || ''} ${code}`.toLocaleLowerCase(locale);
        });
        countrySelect.value = countrySelect.value || 'EG';

        search.addEventListener('input', function () {
            const term = search.value.trim().toLocaleLowerCase(locale);
            Array.from(countrySelect.options).forEach(function (option) {
                option.hidden = Boolean(term) && !option.dataset.countrySearch.includes(term);
            });
        });
    });
}

document.addEventListener('DOMContentLoaded', enhanceCountrySelects);
document.addEventListener('htmx:after:swap', enhanceCountrySelects);
