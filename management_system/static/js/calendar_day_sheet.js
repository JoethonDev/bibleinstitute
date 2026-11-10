/* Canonical day sheet for the admin and student calendars.
   Clicking or tapping a day cell (or an agenda row) clones that day's
   server-rendered content into #calendar-day-body, so every meeting/holiday
   action stays server-owned and no second data source exists.
   The sheet opens at every width: a bottom sheet on phone, a centred dialog on
   desktop (psalmodia.css). Bootstrap Modal owns Escape, backdrop dismissal and
   body scroll locking, so no behaviour is reimplemented here. */
(function () {
  if (window.__calendarDaySheetInstalled) return;
  window.__calendarDaySheetInstalled = true;

  var MOBILE_QUERY = '(max-width: 575.98px)';
  var CELL_SELECTOR = '.aw-calmg-cell[data-cal-day], .sd-day[data-cal-day]';
  var CONTENT_SELECTOR = '.aw-calendar-cell-content, .sd-day-body';
  var mq = window.matchMedia(MOBILE_QUERY);

  /* Only the compact phone cell becomes a single button-like target; on wider
     screens the cell keeps its visible meeting/holiday controls, so promoting
     the container to role="button" would mask them for assistive technology. */
  function syncCellAccess() {
    var cells = document.querySelectorAll(CELL_SELECTOR);
    for (var i = 0; i < cells.length; i++) {
      var cell = cells[i];
      if (mq.matches) {
        cell.setAttribute('tabindex', '0');
        cell.setAttribute('role', 'button');
        cell.setAttribute('aria-label', cell.getAttribute('data-cal-label') || '');
      } else {
        cell.removeAttribute('tabindex');
        cell.removeAttribute('role');
        cell.removeAttribute('aria-label');
      }
    }
  }

  function openDaySheet(trigger) {
    var date = trigger.getAttribute('data-cal-day');
    if (!date) return false;
    var cell = document.querySelector('.aw-calmg-cell[data-cal-day="' + date + '"], .sd-day[data-cal-day="' + date + '"]');
    var content = cell ? cell.querySelector(CONTENT_SELECTOR) : null;
    var modalEl = document.getElementById('calendar-day-modal');
    var body = document.getElementById('calendar-day-body');
    var label = document.getElementById('calendar-day-label');
    if (!content || !body || !label) return false;
    var fragment = content.cloneNode(true);
    var templates = fragment.querySelectorAll('template');
    for (var i = 0; i < templates.length; i++) templates[i].remove();
    body.replaceChildren.apply(body, Array.prototype.slice.call(fragment.childNodes));
    if (!body.querySelector('.aw-calendar-meeting, .aw-calendar-holiday, .sd-day-meeting, .sd-day-holiday, .aw-calendar-add-actions')) {
      return false;
    }
    label.textContent = trigger.getAttribute('data-cal-label') || '';
    // The admin workspace opens the agenda in the shared peek drawer; the
    // student calendar keeps the Bootstrap dialog, which is the only one it
    // renders because the shell script is not loaded there.
    if (window.awDrawer && typeof window.awDrawer.open === 'function') {
      // Move the markup instead of copying it: leaving it in the staging node
      // would duplicate every id while the drawer is open.
      var html = body.innerHTML;
      body.replaceChildren();
      window.awDrawer.open({
        title: label.textContent,
        html: html,
        trigger: trigger
      });
      return true;
    }
    if (!window.bootstrap || !window.bootstrap.Modal || !modalEl) return false;
    if (window.htmx && window.htmx.process) window.htmx.process(body);
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    return true;
  }

  function dayTrigger(target) {
    if (!target || !target.closest) return null;
    var trigger = target.closest('[data-cal-day]');
    if (!trigger || trigger.closest('#calendar-day-modal')) return null;
    var interactive = target.closest('button, a, input, select, textarea');
    if (interactive && interactive !== trigger) return null;
    return trigger;
  }

  document.addEventListener('click', function (event) {
    var trigger = dayTrigger(event.target);
    if (!trigger) return;
    if (openDaySheet(trigger)) event.preventDefault();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var trigger = dayTrigger(event.target);
    if (!trigger || trigger.tagName === 'BUTTON') return;
    if (openDaySheet(trigger)) event.preventDefault();
  });

  if (mq.addEventListener) mq.addEventListener('change', syncCellAccess);
  document.addEventListener('htmx:after:swap', syncCellAccess);
  document.addEventListener('DOMContentLoaded', syncCellAccess);
  syncCellAccess();
})();
