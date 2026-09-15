/**
 * Alpine.js Component: quizJsonImport
 *
 * Admin quiz JSON import panel:
 * - Paste or open a JSON payload and preview it server-side
 * - Load the validated preview (form fields + questions) into the real quiz
 *   form, which then saves the quiz as a draft through the existing POST
 *
 * Contract notes:
 * - All DOM queries resolve from this.$root (the #content x-data element);
 *   this.$el is never used so the component is safe under object spread.
 * - No init() method is defined: a spread-in init() would override
 *   autoSave.initAutoSave / quizForm.init (quizForm() owns init()).
 * - The panel itself never contains a <form>; autoSave() fills the first
 *   <form> found inside #content.
 *
 * @returns {Object} Alpine.js component
 */
function quizJsonImport() {
    const englishSample = {
        "quiz": {"name": "Sample Quiz"},
        "questions": [
            {"type": "mcq", "question": "Which book opens the New Testament?", "grade": 2, "choices": ["Matthew", "Mark", "Luke"], "answer": "Matthew"},
            {"type": "written", "question": "Explain the meaning of grace.", "grade": 3},
            {"type": "complete", "question": "The Word became ___ and dwelt among us.", "grade": 1, "answer": "flesh"},
            {"type": "order_events", "question": "Order the events of Holy Week.", "grade": 4, "items": ["Palm Sunday", "Last Supper", "Crucifixion", "Resurrection"]},
            {"type": "match_related", "question": "Match each Gospel with its author symbol.", "grade": 5, "pairs": [{"left": "Matthew", "right": "Man"}, {"left": "Mark", "right": "Lion"}, {"left": "Luke", "right": "Ox"}, {"left": "John", "right": "Eagle"}]}
        ]
    };

    const arabicSample = {
        "quiz": {"name": "اختبار تجريبي"},
        "questions": [
            {"type": "mcq", "question": "أي سفر يفتتح العهد الجديد؟", "grade": 2, "choices": ["متى", "مرقس", "لوقا"], "answer": "متى"},
            {"type": "written", "question": "اشرح معنى النعمة.", "grade": 3},
            {"type": "complete", "question": "الكلمة صار ___ وسكن بيننا.", "grade": 1, "answer": "جسدًا"},
            {"type": "order_events", "question": "رتّب أحداث أسبوع الآلام.", "grade": 4, "items": ["أحد الشعانين", "خميس العهد", "الجمعة العظيمة", "عيد القيامة"]},
            {"type": "match_related", "question": "صل كل إنجيل برمزه.", "grade": 5, "pairs": [{"left": "متى", "right": "إنسان"}, {"left": "مرقس", "right": "أسد"}, {"left": "لوقا", "right": "ثور"}, {"left": "يوحنا", "right": "نسر"}]}
        ]
    };

    return {
        importOpen: false,
        importPayload: '',

        initQuizImport(forceOpen) {
            // The server renders the panel-open flag into x-init so the HTMX
            // URL push order can never leave the import panel closed.
            if (forceOpen === true) {
                this.importOpen = true;
                return;
            }
            const params = new URLSearchParams(window.location.search);
            if (params.get('import') === '1') {
                this.importOpen = true;
            }
        },

        toggleImportPanel() {
            this.importOpen = !this.importOpen;
            if (this.importOpen) {
                const payload = this.$root.querySelector('#quiz-import-payload');
                if (payload && typeof payload.focus === 'function') {
                    payload.focus();
                }
            }
        },

        clearImportResult() {
            const result = this.$root.querySelector('#quiz-import-result');
            if (result) {
                result.replaceChildren();
            }
        },

        clearImport() {
            this.importPayload = '';
            this.clearImportResult();
            const fileInput = this.$root.querySelector('#quiz-import-file');
            if (fileInput) {
                fileInput.value = '';
            }
        },

        insertImportExample() {
            const sample = document.documentElement.lang === 'ar' ? arabicSample : englishSample;
            this.importPayload = JSON.stringify(sample, null, 2);
            this.clearImportResult();
        },

        loadImportFile(event) {
            const input = event.target;
            const file = input && input.files && input.files[0];
            if (!file) {
                return;
            }
            if (file.size > 2 * 1024 * 1024) {
                this.notify(gettext('The selected file is too large to import.'), 'danger');
                input.value = '';
                return;
            }
            const reader = new FileReader();
            reader.onerror = () => {
                this.notify(gettext('Could not read the selected file.'), 'danger');
                input.value = '';
            };
            reader.onload = () => {
                this.importPayload = String(reader.result || '');
                this.clearImportResult();
                input.value = '';
            };
            reader.readAsText(file, 'utf-8');
        },

        handleQuizImportClick(event) {
            if (event.target.closest('[data-action="quiz-import-load"]')) {
                event.preventDefault();
                this.applyQuizImport();
            }
        },

        notify(message, type) {
            if (window.Alpine && Alpine.store('notifications')) {
                Alpine.store('notifications').add(message, type || 'info');
            }
        },

        applyQuizImport() {
            const result = this.$root.querySelector('#quiz-import-result');
            if (!result) {
                return;
            }
            const dataElement = result.querySelector('#quiz-import-data');
            if (!dataElement) {
                return;
            }

            let payload;
            try {
                payload = JSON.parse(dataElement.textContent);
            } catch (error) {
                console.error('quizJsonImport: invalid payload data', error);
                return;
            }

            const form = this.$root.querySelector('form.admin-form');
            if (!form) {
                return;
            }

            const quiz = (payload && typeof payload.quiz === 'object' && payload.quiz !== null)
                ? payload.quiz
                : {};
            const fieldMappings = [
                { key: 'name', selector: '#quiz_name' },
                { key: 'course_offering', selector: '#course_offering' },
                { key: 'quiz_type', selector: '#quiz_type' },
                { key: 'status', selector: '#quiz-status' },
                { key: 'opening_date', selector: '#opening_date' },
                { key: 'closing_date', selector: '#closing_date' }
            ];
            const selectKeys = ['course_offering', 'quiz_type', 'status'];
            const failed = [];

            fieldMappings.forEach(({ key, selector }) => {
                const rawValue = quiz[key];
                if (rawValue === undefined || rawValue === null || rawValue === '') {
                    return;
                }
                const value = String(rawValue);
                const field = form.querySelector(selector);
                if (!field) {
                    failed.push(key);
                    return;
                }
                if (selectKeys.includes(key)) {
                    const option = Array.prototype.find.call(
                        field.options,
                        candidate => candidate.value === value
                    );
                    if (!option) {
                        failed.push(key);
                        return;
                    }
                }
                field.value = value;
                field.dispatchEvent(new Event('input', { bubbles: true }));
                field.dispatchEvent(new Event('change', { bubbles: true }));
            });

            const preview = result.querySelector('#quiz-import-preview');
            const previewQuestions = preview && preview.querySelector('.questions-container');
            const formQuestions = form.querySelector('.questions-container');
            if (previewQuestions && formQuestions) {
                formQuestions.innerHTML = previewQuestions.innerHTML;
                if (typeof this.reindexQuestions === 'function') {
                    this.reindexQuestions();
                }
            }

            this.clearImportResult();

            if (failed.length === 0) {
                this.notify(gettext('Quiz data loaded into the form. Review it, then submit to save the draft.'), 'success');
            } else {
                this.notify(gettext('Some quiz data could not be loaded into the form.'), 'danger');
            }

            if (typeof form.scrollIntoView === 'function') {
                form.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    };
}

// Export as global for template usage
window.quizJsonImport = quizJsonImport;