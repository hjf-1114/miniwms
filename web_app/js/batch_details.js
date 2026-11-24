let batchFieldRequest;

function loadBatchFields() {
  if (!batchFieldRequest) {
    batchFieldRequest = api("GET", "/api/batch/fields").then(data => data.fields).catch(error => {
      batchFieldRequest = null;
      throw error;
    });
  }
  return batchFieldRequest;
}

function batchDetails(fields, info) {
  return `<dl class="batch-field-grid">${fields.map(f =>
    `<div><dt>${esc(f.label)}</dt><dd>${esc(info?.[f.name] || "—")}</dd></div>`).join("")}</dl>`;
}
