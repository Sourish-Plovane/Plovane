const form = document.querySelector('#beta-form');

if (form) {
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const age = form.elements.age.value;
    const ageMessage = document.querySelector('#age-message');
    const formMessage = document.querySelector('#form-message');

    if (age === 'under13') {
      ageMessage.textContent = 'You must be at least 13 to request private beta access.';
      formMessage.textContent = '';
      return;
    }

    if (age !== '13plus' || !document.querySelector('#consent').checked) {
      formMessage.textContent = 'Confirm you are 13 or older and agree to the Terms before continuing.';
      return;
    }

    window.location.href = 'mailto:info@plovane.com?subject=Plovane%20beta%20access';
  });
}
