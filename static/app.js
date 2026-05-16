(function () {
  'use strict';

  var cards        = Array.from(document.querySelectorAll('.card'));
  var searchInput  = document.getElementById('search');
  var chassisBtns  = document.querySelectorAll('.chassis-btn');
  var resultsEl    = document.getElementById('results-label');
  var emptyState   = document.getElementById('empty-state');
  var grid         = document.getElementById('listing-grid');

  var currentTab     = 'part';
  var currentChassis = '';

  /* ── Core filter ─────────────────────────────────── */
  function applyFilters() {
    var term    = searchInput.value.toLowerCase().trim();
    var visible = 0;

    cards.forEach(function (card) {
      var typeOk    = card.dataset.type === currentTab;
      var chassisOk = !currentChassis ||
                      card.dataset.chassis.split(',').indexOf(currentChassis) !== -1;
      var searchOk  = !term || card.dataset.title.indexOf(term) !== -1;
      var show      = typeOk && chassisOk && searchOk;

      card.style.display = show ? '' : 'none';
      if (show) visible++;
    });

    if (resultsEl) {
      resultsEl.textContent =
        visible.toLocaleString() + ' LISTING' + (visible !== 1 ? 'S' : '');
    }

    if (emptyState) emptyState.style.display = visible === 0 ? 'block' : 'none';
    if (grid)       grid.style.display       = visible === 0 ? 'none'  : '';
  }

  /* ── Tab switching — syncs header nav + content tabs ─ */
  document.querySelectorAll('[data-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = btn.dataset.tab;
      if (!tab) return;           /* About button has no data-tab */
      currentTab = tab;

      document.querySelectorAll('[data-tab]').forEach(function (el) {
        if (el.dataset.tab) {
          el.classList.toggle('active', el.dataset.tab === tab);
        }
      });

      applyFilters();
    });
  });

  /* ── Chassis pills ───────────────────────────────── */
  chassisBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      chassisBtns.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentChassis = btn.dataset.chassis;
      applyFilters();
    });
  });

  /* ── Search ──────────────────────────────────────── */
  searchInput.addEventListener('input', applyFilters);

  /* ── Init ────────────────────────────────────────── */
  applyFilters();
}());
