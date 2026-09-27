const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const menuButton = document.querySelector('[data-menu-toggle]');
const menu = document.querySelector('#site-menu');
let menuPreviousFocus = null;

function setMenu(open) {
  if (!menu || !menuButton) return;
  menu.hidden = !open;
  menuButton.setAttribute('aria-expanded', String(open));
  menuButton.querySelector('[data-menu-label]').textContent = open ? 'Close' : 'Menu';
  document.body.classList.toggle('menu-open', open);
  if (open) {
    menuPreviousFocus = document.activeElement;
    menu.querySelector('a')?.focus();
  } else if (menuPreviousFocus && menu.contains(document.activeElement)) {
    menuPreviousFocus.focus();
  }
}
menuButton?.addEventListener('click', () => setMenu(menu.hidden));
menu?.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => setMenu(false)));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && menu && !menu.hidden) setMenu(false);
});
document.addEventListener('click', (event) => {
  if (menu && !menu.hidden && !event.target.closest('.site-header')) setMenu(false);
});

const reveals = document.querySelectorAll('.reveal');
if (reducedMotion || !('IntersectionObserver' in window)) {
  reveals.forEach((element) => element.classList.add('is-visible'));
} else {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.08, rootMargin: '0px 0px -5% 0px' });
  reveals.forEach((element) => observer.observe(element));
}

const featureTabs = [...document.querySelectorAll('[data-feature-tab]')];
const featurePanels = [...document.querySelectorAll('[data-feature-panel]')];
let activeFeature = 0;

function selectFeature(index, focus = false) {
  activeFeature = (index + featureTabs.length) % featureTabs.length;
  featureTabs.forEach((tab, tabIndex) => {
    const selected = tabIndex === activeFeature;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus();
  });
  featurePanels.forEach((panel, panelIndex) => { panel.hidden = panelIndex !== activeFeature; });
  const count = document.querySelector('[data-feature-count]');
  if (count) count.textContent = String(activeFeature + 1).padStart(2, '0') + ' / ' + String(featureTabs.length).padStart(2, '0');
}
featureTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => selectFeature(index));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Home') selectFeature(0, true);
    else if (event.key === 'End') selectFeature(featureTabs.length - 1, true);
    else selectFeature(activeFeature + (event.key === 'ArrowRight' ? 1 : -1), true);
  });
});
document.querySelector('[data-feature-prev]')?.addEventListener('click', () => selectFeature(activeFeature - 1));
document.querySelector('[data-feature-next]')?.addEventListener('click', () => selectFeature(activeFeature + 1));

const installTabs = [...document.querySelectorAll('[data-install-tab]')];
const installPanels = [...document.querySelectorAll('[data-install-panel]')];
function selectInstall(index, focus = false) {
  installTabs.forEach((tab, tabIndex) => {
    const selected = tabIndex === index;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus();
  });
  installPanels.forEach((panel, panelIndex) => { panel.hidden = panelIndex !== index; });
}
installTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => selectInstall(index));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? installTabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + installTabs.length) % installTabs.length;
    selectInstall(next, true);
  });
});

if (!reducedMotion && window.matchMedia('(pointer:fine)').matches) {
  document.querySelectorAll('[data-tilt]').forEach((card) => {
    card.addEventListener('pointermove', (event) => {
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      card.style.animationPlayState = 'paused';
      card.style.transform = 'translateY(-16px) rotate(' + getComputedStyle(card).getPropertyValue('--rot') + ') rotateY(' + (x * 9) + 'deg) rotateX(' + (-y * 9) + 'deg)';
    });
    card.addEventListener('pointerleave', () => {
      card.style.transform = '';
      card.style.animationPlayState = '';
    });
  });
}
const year = document.querySelector('#year');
if (year) year.textContent = String(new Date().getFullYear());
