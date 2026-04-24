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
                question: 'Question',
                questionPlaceholder: 'Enter the question text here...?',
                grade: 'Grade',
                anotherChoice: 'Add another choice...',
                correctAnswer: 'Correct Answer:',
                delete: 'Delete',
                answerOptions: 'Answer Options',
                studentAnswer: 'Student will write their answer here...',
                studentComplete: 'Student will complete this...',
                answer: 'Answer'
            },
            ar: {
                questionType: 'نوع السؤال:',
                mcq: 'اختيار من متعدد',
                written: 'مقالي',
                complete: 'إكمال الفراغ',
                question: 'السؤال',
                questionPlaceholder: 'أدخل نص السؤال هنا...؟',
                grade: 'الدرجة',
                anotherChoice: 'أضف اختيار آخر...',
                correctAnswer: 'الإجابة الصحيحة:',
                delete: 'حذف',
                answerOptions: 'خيارات الإجابة',
                studentAnswer: 'سيكتب الطالب إجابته هنا...',
                studentComplete: 'سيكمل الطالب هذا...',
                answer: 'الإجابة'
            }
        },

        init() {
            // Initialize existing question cards
            this.$el.querySelectorAll('.question-card').forEach(card => {
                this.addEventListenersToQuestion(card);
            });
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
            return this.$el.querySelectorAll('.question-card').length;
        },

        /**
         * Create a new question
         */
        addQuestion() {
            const questionNumber = this.getQuestionNumber();
            const container = this.$el.querySelector('.questions-container');
            
            const questionCard = document.createElement('div');
            questionCard.className = 'card shadow-sm border-0 mb-4 question-card';
            
            questionCard.innerHTML = `
                <div class="card-body p-4 position-relative">
                    <input type="hidden" name="questions[${questionNumber}][id]" value="">
                    <button type="button" class="btn btn-danger btn-sm position-absolute top-0 end-0 m-3" 
                            @click="removeQuestion($event)" 
                            title="${this.t('delete')}">
                        <i class="fa fa-trash"></i>
                    </button>
                    
                    <div class="mb-3">
                        <label class="form-label fw-semibold text-primary">${this.t('questionType')}</label>
                        <select class="form-select question-type" 
                                name="questions[${questionNumber}][type]"
                                @change="changeQuestionType($event)">
                            <option value="mcq">${this.t('mcq')}</option>
                            <option value="written">${this.t('written')}</option>
                            <option value="complete">${this.t('complete')}</option>
                        </select>
                    </div>
                    
                    <div class="row mb-3">
                        <div class="col-12 col-md-8 mb-2 mb-md-0">
                            <label class="form-label fw-semibold">${this.t('question')}</label>
                            <input type="text" value="" required class="form-control" 
                                   name="questions[${questionNumber}][name]" 
                                   placeholder="${this.t('questionPlaceholder')}">
                        </div>
                        <div class="col-12 col-md-4">
                            <label class="form-label fw-semibold">${this.t('grade')}</label>
                            <input type="number" name="questions[${questionNumber}][grade]" 
                                   class="form-control" required value="1" min="0">
                        </div>
                    </div>
                    
                    <div class="answers-section mb-3">
                        ${this.createAnswerForm(questionNumber, 'mcq')}
                    </div>
                    
                    <div class="correct-answer-section">
                        ${this.createAnswerField(questionNumber, 'mcq')}
                    </div>
                </div>
            `;
            
            container.appendChild(questionCard);
            this.addEventListenersToQuestion(questionCard);
        },

        /**
         * Remove a question
         */
        removeQuestion(event) {
            event.currentTarget.closest('.question-card').remove();
        },

        /**
         * Change question type
         */
        changeQuestionType(event) {
            const selectElement = event.target;
            const value = selectElement.value;
            const parentCard = selectElement.closest('.card-body');
            const questionNumber = this.getQuestionIndex(parentCard.closest('.question-card'));

            const answersSection = parentCard.querySelector('.answers-section');
            const correctAnswerSection = parentCard.querySelector('.correct-answer-section');

            answersSection.innerHTML = this.createAnswerForm(questionNumber, value);
            correctAnswerSection.innerHTML = this.createAnswerField(questionNumber, value);
            
            this.addEventListenersToQuestion(parentCard);
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
                    <div class="mcq-choices"></div>
                    <div class="mt-2">
                        <input class="form-control mcq-add" type="text" 
                               placeholder="${this.t('anotherChoice')}"
                               @keydown.enter.prevent="addMCQChoice($event)"
                               @blur="addMCQChoice($event)">
                    </div>
                `;
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
            if (type === 'written') {
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
            const inputElement = event.target;
            const value = inputElement.value.trim();
            
            if (value) {
                const parentCard = inputElement.closest('.card-body');
                const questionNumber = this.getQuestionIndex(parentCard.closest('.question-card'));
                const choicesContainer = parentCard.querySelector('.mcq-choices');
                
                const choiceDiv = document.createElement('div');
                choiceDiv.className = 'd-flex align-items-center mb-2';
                choiceDiv.innerHTML = `
                    <div class="flex-grow-1 me-2">
                        <input class="form-control" type="text" 
                               name="questions[${questionNumber}][choices]" 
                               value="${value}" required>
                    </div>
                    <button type="button" 
                            class="btn btn-outline-danger btn-sm d-flex align-items-center justify-content-center" 
                            style="width: 38px; height: 38px;"
                            @click="$event.currentTarget.closest('.d-flex').remove()">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                `;
                
                choicesContainer.appendChild(choiceDiv);
                inputElement.value = '';
            }
        },

        /**
         * Add event listeners to question card
         */
        addEventListenersToQuestion(questionEl) {
            // MCQ delete choice buttons
            questionEl.querySelectorAll('.mcq-delete-choice').forEach(btn => {
                if (!btn.hasAttribute('data-listener')) {
                    btn.setAttribute('data-listener', 'true');
                    btn.addEventListener('click', (e) => {
                        e.currentTarget.closest('.d-flex').remove();
                    });
                }
            });
        }
    };
}

// Export as global for template usage
window.quizForm = quizForm;
