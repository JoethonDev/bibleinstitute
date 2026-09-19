/* Single-input Egyptian city combobox for the signup city field.
 *
 * The text input stays the only control: while the selected country is Egypt,
 * typing filters a suggestion list using the shared normalized Arabic
 * matching (arabic_search.js). Choosing a suggestion writes the canonical
 * city name into the input; free text stays allowed. On submit, a typed value
 * that normalizes to a known city is replaced with that canonical city name.
 */

function findCanonicalCity(cityNames, value) {
    var normalized = normalizeArabicSearchTerm(value);
    if (!normalized) return null;
    for (var index = 0; index < cityNames.length; index += 1) {
        if (normalizeArabicSearchTerm(cityNames[index]) === normalized) {
            return cityNames[index];
        }
    }
    return null;
}

function enhanceCitySearch() {
    var countrySelect = document.getElementById("id_country");
    var datalist = document.getElementById("egyptian-cities");
    var input = document.getElementById("city");
    if (!countrySelect || !datalist || !input || input.tagName !== "INPUT") return;
    if (input.dataset.cityCombobox === "true") return;
    input.dataset.cityCombobox = "true";

    var cityNames = Array.from(datalist.options)
        .map(function (option) { return option.value; })
        .filter(Boolean);
    var emptyLabel = datalist.dataset.emptyLabel || "";
    var SUGGESTION_LIMIT = 100;

    var list = document.createElement("ul");
    list.id = "city-suggestions";
    list.className = "city-suggestions";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    input.parentNode.classList.add("city-combobox");
    input.parentNode.insertBefore(list, input.nextSibling);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("autocomplete", "off");

    var activeIndex = -1;

    function isEgypt() {
        return (countrySelect.value || "").toUpperCase() === "EG";
    }

    function close() {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
        activeIndex = -1;
    }

    function selectableOptions() {
        return Array.from(list.children).filter(function (item) {
            return item.dataset.empty !== "true";
        });
    }

    function setActive(index) {
        var options = selectableOptions();
        if (!options.length) {
            activeIndex = -1;
            return;
        }
        if (index < 0) index = options.length - 1;
        if (index >= options.length) index = 0;
        activeIndex = index;
        Array.from(list.children).forEach(function (item) {
            item.classList.toggle("is-active", item === options[activeIndex]);
        });
        input.setAttribute("aria-activedescendant", options[activeIndex].id);
        options[activeIndex].scrollIntoView({ block: "nearest" });
    }

    function render(term) {
        var matches = cityNames.filter(function (name) {
            return arabicSearchMatches(name, term);
        });
        list.textContent = "";
        matches.slice(0, SUGGESTION_LIMIT).forEach(function (name, index) {
            var item = document.createElement("li");
            item.id = "city-suggestion-" + index;
            item.className = "city-suggestion";
            item.setAttribute("role", "option");
            item.textContent = name;
            item.addEventListener("mousedown", function (event) {
                event.preventDefault();
                input.value = name;
                close();
            });
            list.appendChild(item);
        });
        if (!matches.length) {
            var empty = document.createElement("li");
            empty.className = "city-suggestion city-suggestion--empty";
            empty.dataset.empty = "true";
            empty.textContent = emptyLabel;
            list.appendChild(empty);
        }
        activeIndex = -1;
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
    }

    function canonicalize() {
        if (!isEgypt()) return;
        var canonical = findCanonicalCity(cityNames, input.value);
        if (canonical) input.value = canonical;
    }

    input.addEventListener("input", function () {
        if (isEgypt()) render(input.value);
        else close();
    });
    input.addEventListener("focus", function () {
        if (isEgypt()) render(input.value);
    });
    input.addEventListener("blur", function () {
        close();
    });
    input.addEventListener("keydown", function (event) {
        if (list.hidden) {
            if (event.key === "ArrowDown" && isEgypt()) {
                event.preventDefault();
                render(input.value);
            }
            return;
        }
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive(activeIndex + 1);
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive(activeIndex - 1);
        } else if (event.key === "Enter") {
            var options = selectableOptions();
            if (activeIndex >= 0 && options[activeIndex]) {
                event.preventDefault();
                input.value = options[activeIndex].textContent;
                close();
            }
        } else if (event.key === "Escape") {
            close();
        }
    });
    if (input.form) input.form.addEventListener("submit", canonicalize);
    countrySelect.addEventListener("change", function () {
        if (!isEgypt()) close();
    });
}

document.addEventListener("DOMContentLoaded", enhanceCitySearch);
document.addEventListener("htmx:after:swap", enhanceCitySearch);