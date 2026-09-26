(function () {
  'use strict';

  const hints = Array.from(document.querySelectorAll('.sg-help'));
  if (!hints.length) return;

  function hide(hint) {
    const trigger = hint.querySelector('.sg-help-trigger');
    const pop = hint._sgPop;
    if (!trigger || !pop) return;
    hint._sgPinned = false;
    pop.classList.remove('is-visible');
    trigger.setAttribute('aria-expanded', 'false');
  }

  function closeHints(except) {
    hints.forEach((hint) => { if (hint !== except) hide(hint); });
  }

  function show(hint) {
    closeHints(hint);
    const trigger = hint.querySelector('.sg-help-trigger');
    const pop = hint._sgPop;
    if (!trigger || !pop) return;
    pop.classList.add('is-visible');
    trigger.setAttribute('aria-expanded', 'true');
    const rect = trigger.getBoundingClientRect();
    const gap = 6;
    const width = pop.offsetWidth;
    const height = pop.offsetHeight;
    pop.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px';
    pop.style.top = (rect.bottom + height + gap <= window.innerHeight - 12
      ? rect.bottom + gap : Math.max(12, rect.top - height - gap)) + 'px';
  }

  hints.forEach((hint) => {
    const trigger = hint.querySelector('.sg-help-trigger');
    const pop = hint.querySelector('.sg-help-pop');
    if (!trigger || !pop) return;
    hint._sgPop = pop;
    hint._sgPinned = false;
    document.body.appendChild(pop);
    hint.addEventListener('pointerenter', (event) => {
      if (event.pointerType === 'mouse') show(hint);
    });
    hint.addEventListener('pointerleave', () => {
      if (!hint._sgPinned && document.activeElement !== trigger) hide(hint);
    });
    trigger.addEventListener('focus', () => show(hint));
    trigger.addEventListener('blur', () => {
      if (!hint._sgPinned) hide(hint);
    });
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      if (hint._sgPinned) hide(hint);
      else { hint._sgPinned = true; show(hint); }
    });
  });

  document.addEventListener('pointerdown', (event) => {
    if (!event.target.closest('.sg-help')) closeHints();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeHints();
  });
  window.addEventListener('scroll', () => closeHints(), true);
  window.addEventListener('resize', () => closeHints());
})();
