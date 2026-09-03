(function () {
    'use strict';

    function init() {
        var modal = document.getElementById('offeringEditModal');
        if (!modal || modal.dataset.bound === 'true') return;
        modal.dataset.bound = 'true';
        modal.addEventListener('show.bs.modal', function (event) {
            var trigger = event.relatedTarget;
            if (!trigger) return;
            var form = document.getElementById('offeringEditForm');
            var course = document.getElementById('edit-offering-course');
            var instructor = document.getElementById('edit-offering-instructor');
            var status = document.getElementById('edit-offering-status');
            var name = document.getElementById('offeringEditName');
            if (form) form.action = trigger.dataset.offeringAction || '';
            if (course) course.value = trigger.dataset.offeringCourse || '';
            if (instructor) instructor.value = trigger.dataset.offeringInstructor || '';
            if (status) status.value = trigger.dataset.offeringStatus || 'draft';
            if (name) name.textContent = trigger.dataset.offeringName ? ': ' + trigger.dataset.offeringName : '';
        });
    }

    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('htmx:afterSwap', init);
}());
