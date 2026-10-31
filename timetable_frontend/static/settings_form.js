// Settings fields (templates/_settings_fields.html): live download file name,
// years from the course file, and greying out days that are off.
(function () {
    // Years from the file: the number is read-only while the box is ticked.
    const yearsAuto = document.getElementById('degreeYearsAuto');
    const years = document.getElementById('degreeYears');
    const name = document.getElementById('programName');
    const exportName = document.getElementById('exportName');
    function updateExportName() {
        if (!exportName) return;
        const n = (name.value || '').replace(/[\\/:*?"<>|]+/g, ' ').trim().replace(/\s+/g, ' ');
        const y = parseInt(years.value, 10) || 3;
        exportName.textContent = (n ? `מערכת שעות ${n} — ${y} שנים` : `מערכת שעות — ${y} שנים`) + '.html';
    }
    if (yearsAuto && years) yearsAuto.addEventListener('change', () => {
        years.readOnly = yearsAuto.checked;
        if (yearsAuto.checked && yearsAuto.dataset.fileYears) years.value = yearsAuto.dataset.fileYears;
        updateExportName();
    });
    if (name) name.addEventListener('input', updateExportName);
    if (years) years.addEventListener('input', updateExportName);

    // A day that is off greys out its hours.
    document.querySelectorAll('[data-day-toggle]').forEach(box => box.addEventListener('change', () => {
        box.closest('tr').classList.toggle('is-off', !box.checked);
    }));
})();
