/* MiniWMS 通用弹窗表单组件（零依赖，需先于本文件加载 api.js 以使用 esc）。
 *
 * openForm(cfg):
 *   cfg = {
 *     title:      弹窗标题
 *     fields:     [{ name, label, type:'text'|'email'|'number'|'select'|'textarea'|'lines',
 *                    required, autocode:true, autocodePrefix:'OWN', nullable:true,
 *                    coerce:'int', options:[{value,label}], placeholder, full:true,
 *                    lineFields:[...] (仅 type==='lines') }]
 *     values:     预填对象（编辑时）
 *     onSubmit:   async (payload) => void  成功关闭弹窗；失败抛出 Error 显示
 *     submitText: 提交按钮文案（默认"保存"）
 *   }
 */

function genCode(prefix) {
  const d = new Date();
  const ymd = `${d.getFullYear() % 100}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  const rnd = Math.floor(Math.random() * 9000 + 1000);
  return `${prefix}-${ymd}-${rnd}`;
}

function modalFieldHtml(f, val) {
  const req = f.required ? "required" : "";
  const ph = f.placeholder ? `placeholder="${esc(f.placeholder)}"` : "";
  let control;
  if (f.type === "select") {
    const opts = (f.options || []).map((o) => {
      const sel = (o.value === "" && (val === "" || val === null || val === undefined)) || String(o.value) === String(val);
      return `<option value="${esc(o.value)}" ${sel ? "selected" : ""}>${esc(o.label)}</option>`;
    }).join("");
    control = `<select name="${f.name}">${opts}</select>`;
  } else if (f.type === "textarea") {
    control = `<textarea name="${f.name}" ${ph} ${req}>${esc(val)}</textarea>`;
  } else if (f.type === "lines") {
    control = `<div class="lines-editor" data-name="${f.name}"></div>`;
  } else if (f.type === "region") {
    control = `<div class="region-field" data-name="${f.name}">` +
      `<select name="province"><option value="">省/直辖市</option></select>` +
      `<select name="city"><option value="">市</option></select>` +
      `<input name="district" placeholder="区/县" value=""></div>`;
  } else if (f.type === "date") {
    control = `<input type="date" name="${f.name}" value="${esc(val)}">`;
  } else {
    const typ = f.type === "number" ? "number" : (f.type === "email" ? "email" : "text");
    const extra = f.type === "number" ? 'step="0.01"' : "";
    control = `<input name="${f.name}" type="${typ}" ${ph} ${req} ${extra} value="${esc(val)}">`;
  }
  const hint = f.autocode ? '<span class="muted text-sm">留空自动生成</span>' : "";
  return `<div class="${f.full ? "full" : ""}"><label>${esc(f.label)}${f.required ? " *" : ""}</label>${control}${hint}</div>`;
}

function collectLines(container, lineFields) {
  const rows = container.querySelectorAll(".line-row");
  const out = [];
  rows.forEach((row) => {
    const obj = {};
    lineFields.forEach((lf) => {
      let v = row.querySelector(`[name="${lf.name}"]`).value;
      if (lf.coerce === "int" || lf.type === "number") v = v === "" ? "" : Number(v);
      obj[lf.name] = v;
    });
    out.push(obj);
  });
  return out;
}

function renderLinesEditor(container, lineFields, initial) {
  container.innerHTML = "";
  const head = document.createElement("div");
  head.className = "lines-head";
  head.innerHTML = lineFields.map((lf) => `<span>${esc(lf.label)}</span>`).join("") + "<span></span>";
  container.appendChild(head);
  const addRow = (vals) => {
    const row = document.createElement("div");
    row.className = "line-row";
    row.innerHTML = lineFields.map((lf) => {
      const val = vals ? vals[lf.name] : "";
      if (lf.type === "select") {
        const opts = (lf.options || []).map((o) => {
          const sel = (o.value === "" && (val === "" || val === null || val === undefined)) || String(o.value) === String(val);
          return `<option value="${esc(o.value)}" ${sel ? "selected" : ""}>${esc(o.label)}</option>`;
        }).join("");
        return `<select name="${lf.name}">${opts}</select>`;
      }
      return `<input name="${lf.name}" type="${lf.type === "number" ? "number" : "text"}" ${lf.type === "number" ? 'step="0.01"' : ""} value="${esc(val)}" placeholder="${esc(lf.label)}">`;
    }).join("") + `<button type="button" class="btn danger sm" data-del>删</button>`;
    row.querySelector("[data-del]").addEventListener("click", () => row.remove());
    container.appendChild(row);
  };
  (initial && initial.length ? initial : [null]).forEach((v) => addRow(v));
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "btn secondary sm mt8";
  addBtn.textContent = "+ 添加行";
  addBtn.addEventListener("click", () => addRow(null));
  container.appendChild(addBtn);
}

function openForm(cfg) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const body = (cfg.fields || []).map((f) => modalFieldHtml(f, cfg.values ? cfg.values[f.name] : "")).join("");
  const widthStyle = cfg.width ? ` style="width:min(${cfg.width}px,100%)"` : "";
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true"${widthStyle}>
      <div class="modal-head"><h3>${esc(cfg.title || "")}</h3><button class="modal-x" type="button" aria-label="关闭">×</button></div>
      <form class="modal-body grid-form" id="modalForm">
        ${body}
        <div class="modal-err" id="modalErr"></div>
        <div class="modal-foot">
          <button type="button" class="btn secondary" data-close>取消</button>
          <button type="submit" class="btn">${esc(cfg.submitText || "保存")}</button>
        </div>
      </form>
    </div>`;
  document.body.appendChild(overlay);
  const form = overlay.querySelector("#modalForm");
  const errEl = overlay.querySelector("#modalErr");

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.hasAttribute("data-close")) closeModal(overlay);
  });
  overlay.querySelector(".modal-x").addEventListener("click", () => closeModal(overlay));
  const onKey = (e) => { if (e.key === "Escape") { closeModal(overlay); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);

  // 行编辑型字段初始化
  (cfg.fields || []).forEach((f) => {
    if (f.type === "lines") {
      const c = form.querySelector(`.lines-editor[data-name="${f.name}"]`);
      renderLinesEditor(c, f.lineFields, cfg.values ? cfg.values[f.name] : null);
    }
    if (f.type === "region" && typeof bindRegionSelects === "function") {
      const c = form.querySelector(`.region-field[data-name="${f.name}"]`);
      const pSel = c.querySelector('[name="province"]');
      const citySel = c.querySelector('[name="city"]');
      const rb = bindRegionSelects(pSel, citySel);
      if (cfg.values) {
        const pv = cfg.values.province || "";
        const cv = cfg.values.city || "";
        const dv = cfg.values.district || "";
        pSel.value = pv;
        rb.fillCities(pv);
        citySel.value = cv;
        c.querySelector('[name="district"]').value = dv;
      }
    }
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    errEl.style.display = "none";
    const fd = new FormData(form);
    const payload = {};
    for (const f of cfg.fields) {
      if (f.type === "lines") {
        payload[f.name] = collectLines(form.querySelector(`.lines-editor[data-name="${f.name}"]`), f.lineFields);
        continue;
      }
      let v = fd.get(f.name);
      if (v === null) continue;
      if (f.type === "region") {
        payload["province"] = (fd.get("province") || "").trim();
        payload["city"] = (fd.get("city") || "").trim();
        payload["district"] = (fd.get("district") || "").trim();
        continue;
      }
      if (f.type === "number" || f.coerce === "int") {
        v = v === "" ? (f.nullable ? null : (f.type === "number" ? 0 : v)) : Number(v);
      }
      if (f.autocode && (v === "" || v === null || v === undefined)) {
        v = genCode(f.autocodePrefix || f.name.toUpperCase());
      }
      if (v === "" && f.nullable) v = null;
      payload[f.name] = v;
    }
    try {
      await cfg.onSubmit(payload);
      closeModal(overlay);
    } catch (e) {
      errEl.textContent = "提交失败：" + e.message;
      errEl.style.display = "block";
    }
  });

  const first = form.querySelector("input, select, textarea");
  if (first) first.focus();
}

function closeModal(overlay) {
  if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
}
