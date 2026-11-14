// Scripts propios (la política CSP no permite scripts en línea)
document.querySelectorAll('select[data-autosubmit]').forEach(function (s) {
  s.addEventListener('change', function () { s.form.submit(); });
});
document.querySelectorAll('tr[data-fill]').forEach(function (tr) {
  tr.addEventListener('click', function () {
    var u = document.getElementById('u'), p = document.getElementById('p');
    u.value = tr.dataset.fill; p.value = tr.dataset.fill + '123'; p.focus();
  });
});
