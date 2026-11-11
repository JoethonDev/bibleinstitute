/* Theme native file inputs without changing their form semantics. */
(function () {
  "use strict";
  const studentPage = document.body.classList.contains("sn-student") || Boolean(document.querySelector(".sd-page"));
  if (!studentPage) return;

  const isArabic = (document.documentElement.lang || "ar").startsWith("ar");
  const T = isArabic ? {
    choose: "اختر ملفا",
    empty: "لم يتم اختيار ملف",
    count: "{count} ملفات",
  } : {
    choose: "Choose file",
    empty: "No file chosen",
    count: "{count} files",
  };

  document.querySelectorAll('input[type="file"]').forEach((input) => {
    if (input.dataset.pwaFileEnhanced === "1") return;
    // Icon-sized pickers (for example the support chat attach button) own their
    // own styling; the full-width themed control would cover their neighbours.
    if (input.dataset.pwaFileSkip === "1") return;
    if (!input.closest(".sd-page") && !document.body.classList.contains("sn-student")) return;
    input.dataset.pwaFileEnhanced = "1";

    const wrapper = document.createElement("span");
    wrapper.className = "pwa-file-control";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    input.classList.add("pwa-file-native");

    const visual = document.createElement("span");
    visual.className = "pwa-file-visual";
    visual.setAttribute("aria-hidden", "true");
    const button = document.createElement("span");
    button.className = "pwa-file-button";
    button.textContent = T.choose;
    const fileName = document.createElement("span");
    fileName.className = "pwa-file-name";
    visual.append(button, fileName);
    wrapper.appendChild(visual);

    function update() {
      const files = input.files;
      if (!files || !files.length) {
        fileName.textContent = T.empty;
        wrapper.classList.remove("has-file");
        return;
      }
      wrapper.classList.add("has-file");
      fileName.textContent = files.length === 1
        ? files[0].name
        : T.count.replace("{count}", String(files.length));
    }

    input.addEventListener("change", update);
    const form = input.form;
    if (form) form.addEventListener("reset", () => window.setTimeout(update, 0));
    update();
  });
})();
