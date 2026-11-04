/* MiniWMS 通用弹窗表单组件（零依赖，需先于本文件加载 api.js 以使用 esc）。
 *
 * openForm(cfg) -> overlay DOM（返回弹窗节点，便于页面脚本做字段联动/实时校验）
 *   cfg.fields: [{ name, label, type, required, default, placeholder, help,
 *                  options, values, full, readonly, ... }]
 *   type: text | email | number | select | textarea | date | region | lines | section
 *     - section: 仅渲染分节标题，不产生 payload
 *     - select: 支持 placeholder 空选项、default 默认值、required
 *     - lines:  lineFields 支持 placeholder/readonly/required；
 *               支持 optionData + autoFill 实现「选商品自动带出档案」，
 *               支持 onLineChange(row) 回调做行内实时校验，
 *               cardLayout=true 时改为带标签的网格卡片布局
 *   cfg.values / cfg.width / cfg.submitText / cfg.onSubmit
 */

function genCode(prefix) {
  const d = new Date();
  const ymd = `${d.getFullYear() % 100}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  const rnd = Math.floor(Math.random() * 9000 + 1000);
  return `${prefix}-${ymd}-${rnd}`;
}

/** 在指定字段下方显示/清除实时校验错误。 */
function setFieldError(overlay, name, msg) {
  const el = overlay.querySelector(`[name="${name}"]`);
  if (!el) return;
  const wrap = el.parentElement;
  let box = wrap.querySelector(".field-err");
  if (!box) {
    box = document.createElement("div");
    box.className = "field-err";
    wrap.appendChild(box);
  }
  box.textContent = msg || "";
  box.classList.toggle("show", !!msg);
}

function modalFieldHtml(f, val) {
  if (f.type === "section") {
    return `<div class="form-section full"><span>${esc(f.label)}</span></div>`;
  }
  const req = f.required ? "required" : "";
  const ph = f.placeholder ? `placeholder="${esc(f.placeholder)}"` : "";
  let control;
  if (f.type === "select") {
    const emptyOpt = f.placeholder ? `<option value="">${esc(f.placeholder)}</option>` : "";
    const opts = (f.options || []).map((o) => {
      const sel = (o.value === "" && (val === "" || val === null || val === undefined)) ||
        String(o.value) === String(val);
      return `<option value="${esc(o.value)}" ${sel ? "selected" : ""}>${esc(o.label)}</option>`;
    }).join("");
    control = `<select name="${f.name}" ${req}>${emptyOpt}${opts}</select>`;
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
    control = `<input type="date" name="${f.name}" value="${esc(val)}" ${req}>`;
  } else {
    const typ = f.type === "number" ? "number" : (f.type === "email" ? "email" : "text");
    const extra = f.type === "number" ? 'step="0.01"' : "";
    const ro = f.readonly ? "readonly" : "";
    control = `<input name="${f.name}" type="${typ}" ${ph} ${req} ${extra} ${ro} value="${esc(val)}">`;
  }
  const hint = f.autocode ? '<span class="muted text-sm">留空自动生成</span>' : "";
  const help = f.help ? `<div class="field-help">${esc(f.help)}</div>` : "";
  return `<div class="${f.full ? "full" : ""}"><label>${esc(f.label)}${f.required ? " *" : ""}</label>${control}${hint}${help}</div>`;
}

function collectLines(container, lineFields) {
  const rows = container.querySelectorAll(".line-row");
  const out = [];
  rows.forEach((row) => {
    const obj = {};
    lineFields.forEach((lf) => {
      const node = row.querySelector(`[name="${lf.name}"]`);
      if (!node) return;
      let v = node.value;
      if (lf.coerce === "int" || lf.type === "number") v = v === "" ? "" : Number(v);
      obj[lf.name] = v;
    });
    out.push(obj);
  });
  return out;
}

function renderLinesEditor(container, lineFields, initial, cfg) {
  cfg = cfg || {};
  container.innerHTML = "";
  const card = !!cfg.cardLayout;
  if (!card) {
    const head = document.createElement("div");
    head.className = "lines-head";
    head.innerHTML = lineFields.map((lf) => `<span>${esc(lf.label)}</span>`).join("") + "<span></span>";
    container.appendChild(head);
  }
  const autoFill = cfg.autoFill || {};

  const addRow = (vals) => {
    const row = document.createElement("div");
    row.className = "line-row" + (card ? " card-row" : "");
    row.innerHTML = lineFields.map((lf) => {
      const val = vals ? vals[lf.name] : "";
      let control;
      if (lf.type === "select") {
        const emptyOpt = lf.placeholder ? `<option value="">${esc(lf.placeholder)}</option>` : "";
        const opts = (lf.options || []).map((o) => {
          const sel = (o.value === "" && (val === "" || val === null || val === undefined)) ||
            String(o.value) === String(val);
          return `<option value="${esc(o.value)}" ${sel ? "selected" : ""}>${esc(o.label)}</option>`;
        }).join("");
        control = `<select name="${lf.name}" title="${esc(lf.label)}" ${lf.required ? "required" : ""}>${emptyOpt}${opts}</select>`;
      } else {
        const typ = lf.type === "number" ? "number" : (lf.type === "date" ? "date" : "text");
        const step = lf.type === "number" ? 'step="0.01"' : "";
        control = `<input name="${lf.name}" type="${typ}" ${step} value="${esc(val)}" ` +
          `placeholder="${esc(lf.label)}" title="${esc(lf.label)}" ` +
          `${lf.required ? "required" : ""} ${lf.readonly ? "readonly" : ""}>`;
      }
      return card
        ? `<label class="lf"><span>${esc(lf.label)}</span>${control}</label>`
        : control;
    }).join("") +
      `<button type="button" class="btn danger sm" data-del title="删除本行">删</button>` +
      (card ? `<div class="line-err"></div>` : `<div class="line-err"></div>`);
    row.querySelector("[data-del]").addEventListener("click", () => row.remove());

    // 选商品 → 自动带出商品档案字段
    Object.keys(autoFill).forEach((trigger) => {
      const lf = lineFields.find((x) => x.name === trigger);
      const sel = row.querySelector(`[name="${trigger}"]`);
      if (!sel || !lf) return;
      sel.addEventListener("change", () => {
        const src = (lf.optionData || {})[sel.value] || {};
        const map = autoFill[trigger];
        Object.keys(map).forEach((target) => {
          const input = row.querySelector(`[name="${target}"]`);
          if (input) input.value = src[map[target]] ?? "";
        });
        if (typeof cfg.onLineChange === "function") cfg.onLineChange(row);
      });
    });
    // 其余控件变化也触发回调（用于归属实时校验）
    row.querySelectorAll("input, select").forEach((node) => {
      node.addEventListener("change", () => {
        if (typeof cfg.onLineChange === "function") cfg.onLineChange(row);
      });
    });
    container.appendChild(row);
    if (typeof cfg.onLineAdd === "function") cfg.onLineAdd(row);
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
  const body = (cfg.fields || []).map((f) =>
    modalFieldHtml(f, cfg.values ? cfg.values[f.name] : (f.default !== undefined ? f.default : ""))
  ).join("");
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

  // 行编辑型 / 地区复合字段初始化
  (cfg.fields || []).forEach((f) => {
    if (f.type === "lines") {
      const c = form.querySelector(`.lines-editor[data-name="${f.name}"]`);
      renderLinesEditor(c, f.lineFields, cfg.values ? cfg.values[f.name] : null, f);
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
      if (f.type === "section") continue;
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
  return overlay;
}

function closeModal(overlay) {
  if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
}
