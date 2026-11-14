/*
Input: Navigation clicks and page load
Output: Mobile menu state and current copyright year
Processing: Opens and closes the menu and fills in the year
Changed by: Aarav
Date: 2026-11-14
*/

const menuButton = document.querySelector('.menu-toggle');
const mainNav = document.querySelector('#main-nav');
const yearLabel = document.querySelector('#year');

if (yearLabel) {
  yearLabel.textContent = new Date().getFullYear();
}

if (menuButton && mainNav) {
  const closeMenu = () => {
    menuButton.setAttribute('aria-expanded', 'false');
    mainNav.classList.remove('is-open');
  };

  menuButton.addEventListener('click', () => {
    const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
    menuButton.setAttribute('aria-expanded', String(!isOpen));
    mainNav.classList.toggle('is-open', !isOpen);
  });

  mainNav.addEventListener('click', (event) => {
    if (event.target.closest('a')) closeMenu();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenu();
  });

  document.addEventListener('click', (event) => {
    if (!mainNav.contains(event.target) && !menuButton.contains(event.target)) closeMenu();
  });
}
