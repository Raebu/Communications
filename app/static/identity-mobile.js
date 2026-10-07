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
      status.textContent = 'Identity submitted. You can return to Raeburn Connect on your computer.';
      start.hidden = true;
      fallback.hidden = true;
    } catch (error) {
      status.textContent = error.message || 'Unable to start identity verification.';
      fallback.hidden = !fallback.href;
    } finally {
      start.disabled = false;
    }
  });
});
