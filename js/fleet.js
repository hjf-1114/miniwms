/* M31 运力主数据页面公共工具：字典加载、状态/效期标签、效期预警表渲染。
   依赖先加载 api.js（提供 api()/esc()），零第三方依赖。 */
(function () {
  const S = { dict: null, owners: [], carriers: [] };

  async function init() {
    if (S.dict) return S.dict;
    const [dict, ow, car] = await Promise.all([
      api("GET", "/api/fleet/dict"),
      api("GET", "/api/owners?page_size=100"),
      api("GET", "/api/carriers?page_size=100"),
    ]);
    S.dict = dict;
    S.owners = ow.owners || [];
    S.carriers = car.carriers || [];
    return S.dict;
  }

  function state() { return S; }

  function fillSelect(id, items, placeholder) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = (placeholder ? `<option value="">${esc(placeholder)}</option>` : "") +
      (items || []).map(x => `<option value="${esc(x.value)}">${esc(x.label)}</option>`).join("");
  }

  function ownerOptions(withShared) {
    const out = (withShared ? [{ value: "", label: "（全系统共享）" }] : [])
      .concat(S.owners.map(o => ({ value: o.id, label: o.code + " · " + o.name })));
    return out;
  }

  function carrierOptions(withPlaceholder) {
    const out = (withPlaceholder ? [{ value: "", label: "（不关联承运商）" }] : [])
      .concat(S.carriers.map(c => ({ value: c.id, label: c.code + " · " + c.name })));
    return out;
  }

  function ownerText(id) {
    if (id === null || id === undefined || id === "") return "全系统共享";
    const o = S.owners.find(x => String(x.id) === String(id));
    return o ? (o.code + " · " + o.name) : ("#" + id);
  }

  function carrierText(id) {
    if (id === null || id === undefined || id === "") return "—";
    const c = S.carriers.find(x => String(x.id) === String(id));
    return c ? (c.code + " · " + c.name) : ("#" + id);
  }

  function statusTag(status) {
    const active = status === "active";
    return `<span class="tag ${active ? "ok" : "off"}">${active ? "启用" : "停用"}</span>`;
  }

  function level(raw, days) {
    const text = String(raw || "").trim();
    if (!text) return "none";
    const d = new Date(text.slice(0, 10) + "T00:00:00Z");
    if (isNaN(d.getTime())) return "none";
    const now = new Date();
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    const left = Math.floor((d.getTime() - today) / 86400000);
    if (left < 0) return "expired";
    if (left <= Number(days || 30)) return "soon";
    return "normal";
  }

  function levelTag(raw, days) {
    const lv = level(raw, days);
    const map = {
      expired: ["已过期", "danger"], soon: ["临近到期", "warn"],
      normal: ["正常", "ok"], none: ["未登记", "off"],
    };
    const v = map[lv] || map.none;
    return `<span class="tag ${v[1]}">${v[0]}</span>`;
  }

  function renderExpiry(statsId, bodyId, data, days) {
    const stats = document.getElementById(statsId);
    if (stats) {
      stats.innerHTML = (data.stats || []).map(s =>
        `<div class="stat ${esc(s.level)}"><span>${esc(s.label)}</span><b>${s.count}</b></div>`
      ).join("");
    }
    const body = document.getElementById(bodyId);
    if (body) {
      body.innerHTML = (data.items || []).map(x =>
        `<tr><td>${esc(x.kind_label)}</td><td>${esc(x.code)}</td><td>${esc(x.name)}</td>` +
        `<td>${esc(ownerText(x.owner_id))}</td><td>${esc(x.expiry_date || "—")}</td>` +
        `<td>${levelTag(x.expiry_date, days)}</td>` +
        `<td>${x.days_left === null || x.days_left === undefined ? "—" : x.days_left}</td></tr>`
      ).join("") || `<tr><td colspan="7" class="muted">暂无证照效期数据</td></tr>`;
    }
  }

  function queryString(params) {
    const q = new URLSearchParams();
    Object.keys(params || {}).forEach(k => {
      const v = params[k];
      if (v !== "" && v !== null && v !== undefined) q.set(k, v);
    });
    return q.toString();
  }

  window.FLEET_UI = {
    init, state, fillSelect, ownerOptions, carrierOptions, ownerText, carrierText,
    statusTag, level, levelTag, renderExpiry, queryString,
  };
})();
