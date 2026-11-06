window.addEventListener('DOMContentLoaded', () => {
  const choice = window.arguments[0];
  const input = document.getElementById('pages');
  document.getElementById('sourceTitle').textContent = choice.title;
  input.value = '';
  input.focus();
  document.getElementById('translationOptions').addEventListener('submit', event => {
    event.preventDefault();
    try {
      choice.pages = BabelCore.validatePages(input.value);
      window.close();
    } catch (error) {
      document.getElementById('feedback').textContent = error.message;
      input.setAttribute('aria-invalid', 'true');
      input.focus();
    }
  });
  document.getElementById('cancelTranslation').addEventListener('click', () => window.close());
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); window.close(); }
  });
});
