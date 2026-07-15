/**
 * Alpine.js Component: quizForm
 * 
 * Manages quiz form functionality including:
 * - Dynamic question creation/deletion
 * - Question type switching (MCQ, Written, Fill in the Blank)
 * - MCQ choice management
 * - Question numbering and indexing
 * 
 * @returns {Object} Alpine.js component
 */
function quizForm() {
    return {
        // Translations
        translations: {
            en: {
                questionType: 'Question Type:',
                mcq: 'Multiple Choice',
                written: 'Written',
                complete: 'Fill in the Blank',
                orderEvents: 'Order Events',
                matchRelated: 'Match Related',
                question: 'Question',
                questionPlaceholder: 'Enter the question text here...?',
                grade: 'Grade',
                anotherChoice: 'Add another choice...',
                correctAnswer: 'Correct Answer:',
                delete: 'Delete',
                answerOptions: 'Answer Options',
                studentAnswer: 'Student will write their answer here...',
                studentComplete: 'Student will complete this...',
                answer: 'Answer',
                addItem: 'Add item',
                addPair: 'Add pair',
                eventItem: 'Event item',
                leftItem: 'Left item',
                rightItem: 'Right item',
                moveUp: 'Move up',
                moveDown: 'Move down'
            },
            ar: {
                questionType: 'نوع السؤال:',
                mcq: 'اختيار من متعدد',
                written: 'مقالي',
                complete: 'إكمال الفراغ',
                orderEvents: 'ترتيب الأحداث',
                matchRelated: 'المطابقة',
                question: 'السؤال',
                questionPlaceholder: 'أدخل نص السؤال هنا...؟',
                grade: 'الدرجة',
                anotherChoice: 'أضف اختيار آخر...',
                correctAnswer: 'الإجابة الصحيحة:',
                delete: 'حذف',
                answerOptions: 'خيارات الإجابة',
                studentAnswer: 'سيكتب الطالب إجابته هنا...',
                studentComplete: 'سيكمل الطالب هذا...',
                answer: 'الإجابة',
                addItem: 'إضافة عنصر',
                addPair: 'إضافة زوج',
                eventItem: 'عنصر الحدث',
                leftItem: 'العنصر الأول',
                rightItem: 'العنصر الثاني',
                moveUp: 'للأعلى',
                moveDown: 'للأسفل'
            }
        },

        init() {
            this.questionsContainer = this.$el.querySelector('.questions-container');

            if (!this.questionsContainer) {
                console.error('quizForm: .questions-container not found');
                return;
            }

            this.boundHandlers = {
                click: (event) => this.handleClick(event),
                change: (event) => this.handleChange(event),
                input: (event) => this.handleInput(event),
                blur: (event) => this.handleBlur(event),
                keydown: (event) => this.handleKeydown(event),
            };

            this.$el.addEventListener('click', this.boundHandlers.click);
            this.$el.addEventListener('change', this.boundHandlers.change);
            this.$el.addEventListener('input', this.boundHandlers.input);
            this.$el.addEventListener('blur', this.boundHandlers.blur, true);
            this.$el.addEventListener('keydown', this.boundHandlers.keydown);

            this.reindexQuestions();
        },

        /**
         * Get translated string
         */
        t(key) {
            const lang = document.documentElement.lang || 'en';
            return this.translations[lang][key] || this.translations['en'][key];
        },

        /**
         * Get question index from card
         */
        getQuestionIndex(questionCard) {
            return [...this.$el.querySelectorAll('.question-card')].indexOf(questionCard);
        },

        /**
         * Get current number of questions
         */
        getQuestionNumber() {
            return this.getQuestionCards().length;
        },

        getQuestionCards() {
            return [...this.$el.querySelectorAll('.question-card')];
        },

        getQuestionContainer() {
            return this.questionsContainer || this.$el.querySelector('.questions-container');
        },

        escapeHtml(value) {
            return String(value ?? '')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
        },

        buildChoiceRow(questionNumber, choice = '') {
            return `
                <div class="d-flex align-items-center mb-2" data-choice-row>
                    <div class="flex-grow-1 me-2">
                        <input class="form-control choice-input" type="text"
                               name="questions[${questionNumber}][choices]"
                               value="${this.escapeHtml(choice)}">
                    </div>
                    <button type="button"
                            class="btn btn-outline-danger btn-sm btn-icon-sm d-flex align-items-center justify-content-center"
                            data-action="remove-choice"
                            aria-label="${this.t('delete')}">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>
            `;
        },

        buildOrderEventRow(value = '') {
            return `
                <div class="d-flex align-items-center gap-2 mb-2" data-structured-row="order_events">
                    <div class="flex-grow-1">
                        <input class="form-control" type="text" value="${this.escapeHtml(value)}" data-structured-value="order_events" placeholder="${this.t('eventItem')}">
                    </div>
                    <button type="button" class="btn btn-outline-secondary btn-sm" data-action="move-structured-up" aria-label="${this.t('moveUp')}">
                        <i class="fa-solid fa-arrow-up"></i>
                    </button>
                    <button type="button" class="btn btn-outline-secondary btn-sm" data-action="move-structured-down" aria-label="${this.t('moveDown')}">
                        <i class="fa-solid fa-arrow-down"></i>
                    </button>
                    <button type="button" class="btn btn-outline-danger btn-sm" data-action="remove-structured-row" aria-label="${this.t('delete')}">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>
            `;
        },

        buildMatchPairRow(leftValue = '', rightValue = '') {
            return `
                <div class="row g-2 align-items-center mb-2" data-structured-row="match_related">
                    <div class="col-12 col-md-5">
                        <input class="form-control" type="text" value="${this.escapeHtml(leftValue)}" data-structured-left placeholder="${this.t('leftItem')}">
                    </div>
                    <div class="col-12 col-md-5">
                        <input class="form-control" type="text" value="${this.escapeHtml(rightValue)}" data-structured-right placeholder="${this.t('rightItem')}">
                    </div>
                    <div class="col-12 col-md-2 d-grid d-md-flex justify-content-md-end">
                        <button type="button" class="btn btn-outline-danger btn-sm" data-action="remove-structured-row" aria-label="${this.t('delete')}">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                </div>
            `;
        },

        createStructuredEditor(questionNumber, type) {
            if (type === 'order_events') {
                return `
                    <div class="structured-editor" data-structured-editor="order_events">
                        <label class="form-label fw-semibold text-secondary mb-2">${this.t('orderEvents')}</label>
                        <input type="hidden" class="structured-config-input" name="questions[${questionNumber}][config]" value='${this.escapeHtml(JSON.stringify({ items: [] }))}' data-structured-config>
                        <div class="structured-list mb-2" data-structured-list="order_events"></div>
                        <div class="d-flex flex-wrap gap-2">
                            <button type="button" class="btn btn-outline-primary btn-sm" data-action="add-order-item">
                                <i class="fa fa-plus-circle me-1"></i>${this.t('addItem')}
                            </button>
                        </div>
                    </div>
                `;
            }

            if (type === 'match_related') {
                return `
                    <div class="structured-editor" data-structured-editor="match_related">
                        <label class="form-label fw-semibold text-secondary mb-2">${this.t('matchRelated')}</label>
                        <input type="hidden" class="structured-config-input" name="questions[${questionNumber}][config]" value='${this.escapeHtml(JSON.stringify({ pairs: [] }))}' data-structured-config>
                        <div class="structured-list mb-2" data-structured-list="match_related"></div>
                        <div class="d-flex flex-wrap gap-2">
                            <button type="button" class="btn btn-outline-primary btn-sm" data-action="add-match-pair">
                                <i class="fa fa-plus-circle me-1"></i>${this.t('addPair')}
                            </button>
                        </div>
                    </div>
                `;
            }

            return '';
        },

        parseStructuredConfig(configInput) {
            if (!configInput || !configInput.value) {
                return {};
            }

            try {
                return JSON.parse(configInput.value);
            } catch (error) {
                return {};
            }
        },

        syncStructuredQuestionConfig(questionCard) {
            const questionTypeSelect = questionCard.querySelector('.question-type');
            const configInput = questionCard.querySelector('[data-structured-config]');

            if (!questionTypeSelect || !configInput) {
                return;
            }

            const type = questionTypeSelect.value;

            if (type === 'order_events') {
                const items = [...questionCard.querySelectorAll('[data-structured-row="order_events"] [data-structured-value="order_events"]')]
                    .map(input => input.value.trim())
                    .filter(Boolean);
                configInput.value = JSON.stringify({ items });
            } else if (type === 'match_related') {
                const pairs = [];
                questionCard.querySelectorAll('[data-structured-row="match_related"]').forEach(row => {
                    const leftInput = row.querySelector('[data-structured-left]');
                    const rightInput = row.querySelector('[data-structured-right]');
                    const left = leftInput ? leftInput.value.trim() : '';
                    const right = rightInput ? rightInput.value.trim() : '';

                    if (left || right) {
                        pairs.push({ left, right });
                    }
                });
                configInput.value = JSON.stringify({ pairs });
            }
        },

        addStructuredRow(button) {
            const questionCard = button.closest('.question-card');
            const questionType = button.dataset.action === 'add-order-item' ? 'order_events' : 'match_related';
            const list = questionCard.querySelector(`[data-structured-list="${questionType}"]`);

            if (!list) {
                return;
            }

            const rowWrapper = document.createElement('div');
            rowWrapper.innerHTML = questionType === 'order_events' ? this.buildOrderEventRow('') : this.buildMatchPairRow('', '');
            if (rowWrapper.firstElementChild) {
                list.appendChild(rowWrapper.firstElementChild);
                this.syncStructuredQuestionConfig(questionCard);
            }
        },

        removeStructuredRow(button) {
            const row = button.closest('[data-structured-row]');
            const questionCard = button.closest('.question-card');

            if (row) {
                row.remove();
                if (questionCard) {
                    this.syncStructuredQuestionConfig(questionCard);
                }
            }
        },

        moveStructuredRow(button, direction) {
            const row = button.closest('[data-structured-row]');
            const list = row ? row.parentElement : null;

            if (!row || !list) {
                return;
            }

            if (direction === 'up' && row.previousElementSibling) {
                list.insertBefore(row, row.previousElementSibling);
            } else if (direction === 'down' && row.nextElementSibling) {
                list.insertBefore(row.nextElementSibling, row);
            }

            const questionCard = button.closest('.question-card');
            if (questionCard) {
                this.syncStructuredQuestionConfig(questionCard);
            }
        },

        buildQuestionCard(questionNumber, type = 'mcq') {
            return `
                <div class="card shadow-sm border-0 mb-4 question-card" data-question-card>
                    <div class="card-body p-4 position-relative">
                        <input type="hidden" name="questions[${questionNumber}][id]" value="">
                        <button type="button" class="btn btn-danger delete-question mb-3" data-action="remove-question" title="${this.t("delete")}" aria-label="${this.t("delete")}">
                            <i class="fa fa-trash"></i>
                        </button>

                        <div class="mb-3">
                            <label class="form-label fw-semibold text-primary">${this.t("questionType")}</label>
                            <select class="form-select question-type" name="questions[${questionNumber}][type]">
                                <option value="mcq" ${type === "mcq" ? "selected" : ""}>${this.t("mcq")}</option>
                                <option value="written" ${type === "written" ? "selected" : ""}>${this.t("written")}</option>
                                <option value="complete" ${type === "complete" ? "selected" : ""}>${this.t("complete")}</option>
                                <option value="order_events" ${type === "order_events" ? "selected" : ""}>${this.t("orderEvents")}</option>
                                <option value="match_related" ${type === "match_related" ? "selected" : ""}>${this.t("matchRelated")}</option>
                            </select>
                        </div>

                        <div class="row mb-3">
                            <div class="col-12 col-md-8 mb-2 mb-md-0">
                                <label class="form-label fw-semibold">${this.t("question")}</label>
                                <input type="text" value="" required class="form-control" name="questions[${questionNumber}][name]" placeholder="${this.t("questionPlaceholder")}">
                            </div>
                            <div class="col-12 col-md-4">
                                <label class="form-label fw-semibold">${this.t("grade")}</label>
                                <input type="number" name="questions[${questionNumber}][grade]" class="form-control" required value="1" min="0">
                            </div>
                        </div>

                        <div class="answers-section mb-3">
                            ${this.createAnswerForm(questionNumber, type)}
                        </div>

                        <div class="correct-answer-section">
                            ${this.createAnswerField(questionNumber, type)}
                        </div>
                    </div>
                </div>
            `;
        },

        reindexQuestions() {
            this.getQuestionCards().forEach((card, index) => {
                card.dataset.questionIndex = index;

                const idInput = card.querySelector('input[type="hidden"]');
                if (idInput) {
                    idInput.name = `questions[${index}][id]`;
                }

                const typeSelect = card.querySelector('.question-type');
                if (typeSelect) {
                    typeSelect.name = `questions[${index}][type]`;
                }

                const questionInput = card.querySelector('input[name$="[name]"]');
                if (questionInput) {
                    questionInput.name = `questions[${index}][name]`;
                }

                const gradeInput = card.querySelector('input[name$="[grade]"]');
                if (gradeInput) {
                    gradeInput.name = `questions[${index}][grade]`;
                }

                const answerInput = card.querySelector('.correct-answer-section input, .correct-answer-section textarea');
                if (answerInput && answerInput.name !== undefined) {
                    answerInput.name = `questions[${index}][answer]`;
                }

                const structuredConfigInput = card.querySelector('[data-structured-config]');
                if (structuredConfigInput) {
                    structuredConfigInput.name = `questions[${index}][config]`;
                }

                card.querySelectorAll('[data-choice-row] input').forEach(choiceInput => {
                    choiceInput.name = `questions[${index}][choices]`;
                });
            });
        },

        /**
         * Create a new question
         */
        addQuestion() {
            const questionNumber = this.getQuestionNumber();
            const container = this.getQuestionContainer();

            if (!container) {
                console.error('quizForm: cannot add question because the container is missing');
                return;
            }

            const wrapper = document.createElement('div');
            wrapper.innerHTML = this.buildQuestionCard(questionNumber, 'mcq');
            const questionCard = wrapper.firstElementChild;

            if (!questionCard) {
                console.error('quizForm: failed to build question card');
                return;
            }

            container.appendChild(questionCard);
            this.reindexQuestions();
        },

        /**
         * Remove a question
         */
        removeQuestion(button) {
            const questionCard = button.closest('.question-card');
            if (questionCard) {
                questionCard.remove();
                this.reindexQuestions();
            }
        },

        /**
         * Change question type
         */
        changeQuestionType(selectElement) {
            const value = selectElement.value;
            const parentCard = selectElement.closest('.card-body');
            const questionNumber = this.getQuestionIndex(parentCard.closest('.question-card'));

            const answersSection = parentCard.querySelector('.answers-section');
            const correctAnswerSection = parentCard.querySelector('.correct-answer-section');

            answersSection.innerHTML = this.createAnswerForm(questionNumber, value);
            correctAnswerSection.innerHTML = this.createAnswerField(questionNumber, value);

            const questionCard = parentCard.closest('.question-card');
            if (questionCard && (value === 'order_events' || value === 'match_related')) {
                const addButton = questionCard.querySelector(`[data-action="${value === 'order_events' ? 'add-order-item' : 'add-match-pair'}"]`);
                if (addButton) {
                    this.addStructuredRow(addButton);
                }
            }

            if (questionCard) {
                this.syncStructuredQuestionConfig(questionCard);
            }
            this.reindexQuestions();
        },

        handleClick(event) {
            const addQuestionButton = event.target.closest('[data-action="add-question"]');
            if (addQuestionButton) {
                event.preventDefault();
                this.addQuestion();
                return;
            }

            const removeQuestionButton = event.target.closest('[data-action="remove-question"]');
            if (removeQuestionButton) {
                event.preventDefault();
                this.removeQuestion(removeQuestionButton);
                return;
            }

            const removeChoiceButton = event.target.closest('[data-action="remove-choice"]');
            if (removeChoiceButton) {
                event.preventDefault();
                const choiceRow = removeChoiceButton.closest('[data-choice-row]');
                if (choiceRow) {
                    choiceRow.remove();
                    this.reindexQuestions();
                }
                return;
            }

            const structuredActionButton = event.target.closest('[data-action="add-order-item"], [data-action="add-match-pair"], [data-action="remove-structured-row"], [data-action="move-structured-up"], [data-action="move-structured-down"]');
            if (structuredActionButton) {
                event.preventDefault();

                if (structuredActionButton.dataset.action === 'add-order-item' || structuredActionButton.dataset.action === 'add-match-pair') {
                    this.addStructuredRow(structuredActionButton);
                } else if (structuredActionButton.dataset.action === 'remove-structured-row') {
                    this.removeStructuredRow(structuredActionButton);
                } else if (structuredActionButton.dataset.action === 'move-structured-up') {
                    this.moveStructuredRow(structuredActionButton, 'up');
                } else if (structuredActionButton.dataset.action === 'move-structured-down') {
                    this.moveStructuredRow(structuredActionButton, 'down');
                }

                this.reindexQuestions();
                return;
            }
        },

        handleChange(event) {
            const selectElement = event.target.closest('.question-type');
            if (selectElement) {
                this.changeQuestionType(selectElement);
            }

            const structuredField = event.target.closest('.structured-editor input, .structured-editor select');
            if (structuredField) {
                this.syncStructuredQuestionConfig(structuredField.closest('.question-card'));
            }
        },

        handleInput(event) {
            const structuredField = event.target.closest('.structured-editor input, .structured-editor select');
            if (structuredField) {
                this.syncStructuredQuestionConfig(structuredField.closest('.question-card'));
            }
        },

        handleBlur(event) {
            const addChoiceInput = event.target.closest('.mcq-add');
            if (addChoiceInput) {
                this.addMCQChoice(addChoiceInput);
            }
        },

        handleKeydown(event) {
            const addChoiceInput = event.target.closest('.mcq-add');
            if (addChoiceInput && event.key === 'Enter') {
                event.preventDefault();
                this.addMCQChoice(addChoiceInput);
            }
        },

        /**
         * Create answer form based on question type
         */
        createAnswerForm(questionNumber, type = 'mcq') {
            if (type === 'written') {
                return `
                    <label class="form-label fw-semibold text-secondary">${this.t('answer')}</label>
                    <textarea class="form-control" rows="4" disabled 
                              placeholder="${this.t('studentAnswer')}"></textarea>
                `;
            } else if (type === 'mcq') {
                return `
                    <label class="form-label fw-semibold text-secondary mb-2">${this.t('answerOptions')}</label>
                    <div class="mcq-choices" data-choice-list></div>
                    <div class="mt-2">
                        <input class="form-control mcq-add" type="text" 
                               placeholder="${this.t('anotherChoice')}"
                               autocomplete="off">
                    </div>
                `;
            } else if (type === 'order_events' || type === 'match_related') {
                return this.createStructuredEditor(questionNumber, type);
            } else { // complete
                return `
                    <label class="form-label fw-semibold text-secondary">${this.t('answer')}</label>
                    <input class="form-control" type="text" disabled 
                           placeholder="${this.t('studentComplete')}">
                `;
            }
        },

        /**
         * Create correct answer field
         */
        createAnswerField(questionNumber, type = 'mcq') {
            if (type === 'written' || type === 'order_events' || type === 'match_related') {
                return '';
            }
            return `
                <div class="card bg-light border-0 p-3">
                    <label class="form-label fw-semibold text-success mb-2">
                        <i class="fa fa-check-circle me-2"></i>
                        ${this.t('correctAnswer')}
                    </label>
                    <input class="form-control" id="answer_${questionNumber}" 
                           name="questions[${questionNumber}][answer]" value="">
                </div>
            `;
        },

        /**
         * Add MCQ choice
         */
        addMCQChoice(event) {
            const inputElement = event instanceof Event ? event.target : event;
            const value = inputElement.value.trim();
            
            if (value) {
                const parentCard = inputElement.closest('.card-body');
                const questionNumber = this.getQuestionIndex(parentCard.closest('.question-card'));
                const choicesContainer = parentCard.querySelector('[data-choice-list]');
                
                const choiceDiv = document.createElement('div');
                choiceDiv.innerHTML = this.buildChoiceRow(questionNumber, value);
                
                if (choicesContainer && choiceDiv.firstElementChild) {
                    choicesContainer.appendChild(choiceDiv.firstElementChild);
                }
                inputElement.value = '';
                this.reindexQuestions();
            }
        },

        /**
         * Add event listeners to question card
         */
        addEventListenersToQuestion() {}
    };
}

// Export as global for template usage
window.quizForm = quizForm;
