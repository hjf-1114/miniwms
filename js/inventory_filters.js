async function fillInventorySelect(id, resource, label, format) {
  const select = document.getElementById(id);
  const options = [new Option(label, "")];
  for (let page = 1; ; page++) {
    const data = await api("GET", `/api/${resource}?page_size=100&page=${page}`);
    for (const row of data[resource]) options.push(new Option(format(row), row.id));
    if (page * 100 >= data.total || !data[resource].length) break;
  }
  select.replaceChildren(...options);
  select.value = select.dataset.initial || "";
}

function inventoryFilterParams(form) {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(form)) {
    if (value.trim()) params.set(key, value.trim());
  }
  return params;
}
