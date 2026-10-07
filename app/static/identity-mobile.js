document.addEventListener('DOMContentLoaded', () => {
  const card = document.querySelector('[data-identity-token]');
  const start = document.getElementById('identity-modal-start');
  const status = document.getElementById('identity-mobile-status');
  const fallback = document.getElementById('identity-direct-fallback');
  if (!card || !start || !status || !fallback) return;

  const token = card.dataset.identityToken;

  start.addEventListener('click', async () => {
    start.disabled = true;
    status.textContent = 'Preparing Stripe secure verification…';
    fallback.hidden = true;
    try {
      const response = await fetch('/api/company-verification/identity-mobile/' + encodeURIComponent(token) + '/modal', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'X-Requested-With': 'Raeburn'}
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Unable to start identity verification.');

      if (data.redirect_url) {
        fallback.href = data.redirect_url;
        fallback.hidden = false;
      }
      if (data.mode !== 'modal') {
        status.textContent = 'Opening Stripe in this browser…';
        location.assign(data.redirect_url);
        return;
      }

      if (typeof Stripe !== 'function') throw new Error('Stripe secure verification could not load. Try the direct browser option below.');
      const stripe = Stripe(data.publishable_key);
      status.textContent = '';
      const result = await stripe.verifyIdentity(data.client_secret);
      if (result.error) {
        status.textContent = result.error.message || 'Stripe could not complete the verification. Try the direct browser option below.';
        fallback.hidden = false;
        return;
      }

      start.hidden = true;
      fallback.hidden = true;
      const title = document.querySelector('.mobile-handoff-card h1');
      const lead = document.querySelector('.mobile-handoff-card .lead');
      if (title) title.textContent = 'Verification submitted.';
      if (lead) lead.textContent = 'Stripe has received your identity check. We are confirming the result with Companies House now.';
      status.textContent = 'Finishing verification…';

      for (let attempt = 0; attempt < 20; attempt += 1) {
        const complete = await fetch('/api/company-verification/identity-mobile/' + encodeURIComponent(token) + '/complete', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'X-Requested-With': 'Raeburn'}
        });
        const state = await complete.json();
        if (complete.ok && state.status === 'verified') {
          if (title) title.textContent = 'Director identity verified.';
          if (lead) lead.textContent = 'Your director identity has been verified and matched to the company record.';
          status.textContent = 'You can close this page and return to Raeburn Connect on your computer.';
          return;
        }
        if (!complete.ok && state.status === 'held') {
          throw new Error(state.detail || 'The identity result needs attention.');
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
      status.textContent = 'Stripe has your verification. Raeburn Connect is still confirming the result; you can return to your computer.';
    } catch (error) {
      status.textContent = error.message || 'Unable to start identity verification.';
      fallback.hidden = !fallback.href;
    } finally {
      start.disabled = false;
    }
  });
});
