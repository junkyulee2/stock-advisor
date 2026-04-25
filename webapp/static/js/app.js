// 춘규주식 — small client-side helpers (HTMX is the main interactive layer)

document.addEventListener('click', (e) => {
  const pill = e.target.closest('.amt-pill');
  if (!pill) return;
  const group = pill.closest('.amt-pills');
  if (!group) return;
  group.querySelectorAll('.amt-pill').forEach(p => p.classList.remove('on'));
  pill.classList.add('on');
  const hidden = pill.closest('form')?.querySelector('input[name="amount_krw"]');
  if (hidden && pill.dataset.amount) hidden.value = pill.dataset.amount;
});

// Auto-hide toasts after 3.5s
const obs = new MutationObserver(() => {
  document.querySelectorAll('#toast-region .toast').forEach(t => {
    if (!t.dataset.scheduled) {
      t.dataset.scheduled = '1';
      setTimeout(() => t.remove(), 3500);
    }
  });
});
obs.observe(document.body, { childList: true, subtree: true });

// HTMX request feedback: dim button while in-flight
document.body.addEventListener('htmx:beforeRequest', (e) => {
  const btn = e.detail.elt?.querySelector?.('.btn-primary, .btn-sm');
  if (btn) btn.classList.add('loading');
});
document.body.addEventListener('htmx:afterRequest', (e) => {
  document.querySelectorAll('.btn-primary.loading, .btn-sm.loading').forEach(b => b.classList.remove('loading'));
});
