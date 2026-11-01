const form = document.querySelector('#afford-form');
const output = document.querySelector('#result');
const errorBox = document.querySelector('#error');
const submitButton = document.querySelector('#submit-button');
const installmentToggle = document.querySelector('#installment-toggle');
const monthsInput = document.querySelector('#months');
const monthlyInput = document.querySelector('#monthly');
const fileInput = document.querySelector('#image-file');
const flexInput = document.querySelector('[name="flexibility"]');
const flexOutput = document.querySelector('#flex-output');
const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });

flexInput.addEventListener('input', () => { flexOutput.value = `${flexInput.value}%`; });
installmentToggle.addEventListener('change', () => {
  monthsInput.disabled = monthlyInput.disabled = !installmentToggle.checked;
});
fileInput.addEventListener('change', () => {
  document.querySelector('#file-name').textContent = fileInput.files[0]?.name || 'PNG, JPG, or WebP';
});

function val(name) { return form.elements[name].value; }
function num(name) { return Number(val(name) || 0); }
function safeText(value) { return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch])); }
function statePayload() {
  return {
    current_balance: num('balance'), confirmed_monthly_income: num('income'),
    recurring_monthly_expenses: num('recurring'), pending_payments: num('pending'),
    essential_reserve: num('reserve'), as_of: new Date().toISOString().slice(0, 10)
  };
}
function profilePayload() {
  const goals = val('goal') ? [val('goal')] : [];
  const priorities = val('priority') !== 'normal' ? { [val('category')]: val('priority') } : {};
  return {
    user_id: `browser-${localStorage.getItem('dealix-user') || (() => { const id = crypto.randomUUID(); localStorage.setItem('dealix-user', id); return id; })()}`,
    risk_tolerance: val('risk'), payment_preference: val('preference'), income_stability: val('stability'),
    flexible_expense_willingness: Number(val('flexibility')) / 100,
    preferred_minimum_balance: num('minBalance'), active_financial_goals: goals, category_priorities: priorities
  };
}
function expensePayload() {
  return {
    item_name: val('item').trim(), cost: num('cost'), category: val('category'), urgency: val('urgency'),
    installment_available: installmentToggle.checked,
    installment_months: installmentToggle.checked ? Number(monthsInput.value) : null,
    installment_monthly_amount: installmentToggle.checked ? Number(monthlyInput.value) : null
  };
}
function renderResult(data) {
  const label = {
    affordable_now: 'Affordable now', affordable_with_plan: 'Affordable with a plan',
    affordable_later: 'Better to wait', not_affordable: 'Not safe right now'
  }[data.affordability_status] || data.affordability_status;
  const mood = data.affordability_status === 'not_affordable' ? 'no' : data.affordability_status === 'affordable_later' ? 'later' : '';
  const plan = (data.payment_plan || []).map((item) => `<div class="plan-row"><span>${safeText(item.date)} · ${safeText(item.label)}</span><strong>${money.format(item.amount)}</strong></div>`).join('');
  const changes = (data.spending_changes_needed || []).map((item) => `<div>• ${safeText(item.action)} or reduce ${safeText(item.expense_name)}${item.monthly_savings != null ? ` · frees ${money.format(item.monthly_savings)}/month` : ''}</div>`).join('');
  output.innerHTML = `<div class="result-top"><div><span class="section-kicker">YOUR PERSONALIZED CHECK</span><h3>${safeText(val('item'))}</h3></div><span class="status-pill ${mood}">${safeText(label)}</span></div><div class="safe-amount">${money.format(data.amount_safe_to_pay)}</div><div class="safe-label">maximum safe to pay today</div><p class="decision-copy">${safeText(data.decision_explanation)}</p>${plan ? `<div class="result-subhead">Suggested payment plan</div><div class="plan-list">${plan}</div>` : ''}${data.earliest_date_for_full_payment ? `<div class="result-subhead">Earliest safe full-payment date</div><div class="decision-copy">${safeText(data.earliest_date_for_full_payment)}</div>` : ''}${changes ? `<div class="result-subhead">Flexible spending to adjust</div><div class="change-list">${changes}</div>` : ''}`;
  output.hidden = false;
  output.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  output.hidden = true;
  errorBox.hidden = true;
  submitButton.disabled = true;
  submitButton.querySelector('span:first-child').textContent = 'Checking your full forecast…';
  const message = val('message').trim();
  const image = fileInput.files[0];
  try {
    let endpoint = '/decide';
    let body;
    if (message || image) {
      endpoint = '/decide/from-query';
      const query = { profile: profilePayload(), state: statePayload(), fallback_expense: expensePayload(), message: message || null };
      if (image) {
        query.image_base64 = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(new Error('Could not read the selected image.'));
          reader.readAsDataURL(image);
        });
        query.image_media_type = image.type;
      }
      body = query;
    } else {
      body = { profile: profilePayload(), state: statePayload(), expense: expensePayload() };
    }
    const response = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your entries and try again.');
    renderResult(data);
  } catch (error) {
    errorBox.textContent = error.message || 'Unable to reach the DealIX service. Please check that it is running and try again.';
    errorBox.hidden = false;
  } finally {
    submitButton.disabled = false;
    submitButton.querySelector('span:first-child').textContent = 'Check my affordability';
  }
});
