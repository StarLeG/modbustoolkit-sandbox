// «Документация» (TASK-752): копирование примеров кода в буфер обмена.
// Кнопки с data-copy="id" копируют textContent блока <code id>.

function el(id) {
  return document.getElementById(id);
}

async function copyExample(event) {
  const button = event.currentTarget;
  const code = el(button.dataset.copy);
  await navigator.clipboard.writeText(code.textContent);
  const status = el("example-status");
  status.textContent = window.t("Скопировано: {id}.", { id: code.id });
  const oldLabel = button.textContent;
  button.textContent = window.t("Скопировано");
  setTimeout(() => {
    button.textContent = oldLabel;
  }, 1500);
}

function init() {
  for (const button of document.querySelectorAll(".copy-btn")) {
    button.addEventListener("click", copyExample);
  }
}

init();
