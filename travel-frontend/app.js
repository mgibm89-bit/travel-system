// API_URL нь config.js-ээс ирнэ.
const TOKEN_KEY = "travel_admin_token";
const EMAIL_KEY = "travel_admin_email";

const $ = (sel, root = document) => root.querySelector(sel);

// ---------- Токен хадгалах ----------
function getToken() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } }
function saveSession(token, email) {
  try { localStorage.setItem(TOKEN_KEY, token); localStorage.setItem(EMAIL_KEY, email); } catch {}
}
function clearSession() {
  try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EMAIL_KEY); } catch {}
}

// ---------- API дуудах туслах ----------
// Хүсэлт бүрт "Authorization: Bearer <токен>" толгойг автоматаар нэмнэ.
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${API_URL}${path}`, { ...options, headers });
  } catch {
    throw new Error("Backend-тэй холбогдож чадсангүй. Сервер асаалттай эсэхийг шалгана уу.");
  }
  if (res.status === 401 && path !== "/login") {
    logout();
    throw new Error("Дахин нэвтэрнэ үү");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // FastAPI-ийн шалгалтын алдаа (422) нь жагсаалт хэлбэртэй ирдэг
    const msg = Array.isArray(data.detail)
      ? "Талбаруудыг зөв бөглөнө үү: " + data.detail.map((d) => d.loc?.slice(-1)[0]).join(", ")
      : data.detail;
    throw new Error(msg || `Алдаа (${res.status})`);
  }
  return data;
}

// ---------- Туслах функцууд ----------
const fmtNum = (n) => Number(n).toLocaleString("en-US");
const fmtMoney = (n) => `${fmtNum(n)} ₮`;
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

let toastTimer;
function toast(message, type = "ok") {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hide"), type === "err" ? 5000 : 2500);
}

// ---------- Нэвтрэх ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#login-btn");
  const err = $("#login-error");
  err.classList.add("hidden");
  btn.disabled = true;
  btn.textContent = "Түр хүлээнэ үү...";
  try {
    const data = await api("/login", {
      method: "POST",
      body: JSON.stringify({ email: $("#email").value, password: $("#password").value }),
    });
    saveSession(data.access_token, data.email);
    showApp();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Нэвтрэх";
  }
});

function logout() {
  clearSession();
  $("#app-view").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  $("#password").value = "";
}
$("#logout").addEventListener("click", logout);

// =====================================================================
// ЕРӨНХИЙ CRUD ФУНКЦ
// Нэг тохиргоо (config) өгөхөд жагсаалт, хайлт, нэмэх/засах форм,
// устгах товчтой бүтэн хуудас үүсгэнэ. 4 цэс бүгд үүнийг ашиглана.
// =====================================================================
//
// config = {
//   title, subtitle,
//   endpoint: "/tours",            // GET/POST энд, PUT/DELETE нь endpoint/{id}
//   idKey: "tour_id",
//   listPath: () => "/tours",      // (сонголттой) жагсаалт татах зам, шүүлтүүртэй үед
//   columns: [{ key, label, render?(row) }],
//   fields:  [{ key, label, type: "text|number|date|email|select",
//               required?, options?: async () => [{value, label}] }],
//   toolbar?: (reload) => HTML элемент,   // жишээ нь төлөвийн шүүлтүүр
//   rowActions?: (row) => [{ label, cls, onClick }],
//   canDelete?: (row) => bool,
//   deleteConfirm?: (row) => "асуух текст",
// }
async function crudPage(config) {
  const content = $("#content");
  content.innerHTML = `
    <div class="page-head">
      <div><h2>${esc(config.title)}</h2><p class="sub">${esc(config.subtitle)}</p></div>
      <div class="head-actions">
        <span class="toolbar-slot"></span>
        <button class="btn orange" id="add-btn">+ Нэмэх</button>
      </div>
    </div>
    <form class="panel form hidden" id="crud-form" novalidate></form>
    <input class="search" id="search" placeholder="Хайх...">
    <div class="panel table-wrap"><p class="empty">Ачаалж байна...</p></div>`;

  let rows = [];
  let editing = null; // засаж буй мөр (null бол шинээр нэмж байна)
  const form = $("#crud-form");
  const tableWrap = $(".table-wrap");
  const search = $("#search");

  // Select талбаруудын сонголтуудыг нэг удаа ачаална
  const optionCache = {};
  await Promise.all(config.fields.filter((f) => f.options).map(async (f) => {
    try { optionCache[f.key] = await f.options(); } catch { optionCache[f.key] = []; }
  }));

  async function reload() {
    try {
      rows = await api(config.listPath ? config.listPath() : config.endpoint);
      renderTable();
    } catch (ex) {
      tableWrap.innerHTML = `<p class="empty">${esc(ex.message)}</p>`;
    }
  }

  function renderTable() {
    const q = search.value.trim().toLowerCase();
    const shown = q
      ? rows.filter((r) => config.columns.some((c) =>
          String(c.text ? c.text(r) : r[c.key] ?? "").toLowerCase().includes(q)))
      : rows;

    if (!shown.length) {
      tableWrap.innerHTML = `<p class="empty">${q ? "Хайлтад тохирох мөр алга." : "Мэдээлэл алга."}</p>`;
      return;
    }
    tableWrap.innerHTML = `
      <table>
        <thead><tr>
          ${config.columns.map((c) => `<th>${esc(c.label)}</th>`).join("")}<th></th>
        </tr></thead>
        <tbody></tbody>
      </table>
      <p class="count">${shown.length} мөр</p>`;
    const tbody = $("tbody", tableWrap);

    for (const row of shown) {
      const tr = document.createElement("tr");
      tr.innerHTML = config.columns.map((c) =>
        `<td>${c.render ? c.render(row) : esc(row[c.key])}</td>`).join("") +
        `<td class="actions"></td>`;
      const cell = $(".actions", tr);

      const extra = config.rowActions ? config.rowActions(row) : [];
      const actions = [
        ...extra,
        { label: "Засах", cls: "edit", onClick: () => openForm(row) },
        ...((config.canDelete ? config.canDelete(row) : true)
          ? [{ label: "Устгах", cls: "del", onClick: () => remove(row) }] : []),
      ];
      for (const a of actions) {
        const b = document.createElement("button");
        b.className = `mini ${a.cls}`;
        b.textContent = a.label;
        b.addEventListener("click", a.onClick);
        cell.appendChild(b);
      }
      tbody.appendChild(tr);
    }
  }

  function fieldHtml(f, value) {
    const v = value ?? "";
    const req = f.required ? "required" : "";
    if (f.type === "select") {
      const opts = optionCache[f.key] || [];
      return `<select name="${f.key}" ${req}>
        <option value="">Сонгох</option>
        ${opts.map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(v) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}
      </select>`;
    }
    const extra = f.type === "number" ? `min="${f.min ?? 0}" step="${f.step ?? 1}"` : "";
    return `<input name="${f.key}" type="${f.type || "text"}" value="${esc(v)}" ${req} ${extra}>`;
  }

  function openForm(row = null) {
    editing = row;
    form.innerHTML = `
      <div class="fields">
        ${config.fields.map((f) => `
          <label>${esc(f.label)}${f.required ? " *" : ""}
            ${fieldHtml(f, row ? row[f.key] : f.default)}
          </label>`).join("")}
      </div>
      <div class="form-actions">
        <button type="submit" class="btn green">${row ? "Хадгалах" : "Нэмэх"}</button>
        <button type="button" class="btn ghost" id="cancel-btn">Болих</button>
        ${row ? `<span class="muted-inline">#${row[config.idKey]} засаж байна</span>` : ""}
      </div>`;
    form.classList.remove("hidden");
    $("#cancel-btn", form).addEventListener("click", closeForm);
    form.querySelector("input, select")?.focus();
  }

  function closeForm() {
    editing = null;
    form.classList.add("hidden");
    form.innerHTML = "";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.reportValidity()) return;

    const body = {};
    for (const f of config.fields) {
      const raw = form.elements[f.key].value.trim();
      if (raw === "") body[f.key] = null;
      else if (f.type === "number" || f.numeric) body[f.key] = Number(raw);
      else body[f.key] = raw;
    }

    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      if (editing) {
        await api(`${config.endpoint}/${editing[config.idKey]}`, { method: "PUT", body: JSON.stringify(body) });
        toast("Амжилттай хадгаллаа");
      } else {
        const res = await api(config.endpoint, { method: "POST", body: JSON.stringify(body) });
        toast(`Шинээр нэмлээ (#${res[config.idKey]})`);
      }
      closeForm();
      await reload();
      refreshBadges();
    } catch (ex) {
      toast(ex.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  async function remove(row) {
    const id = row[config.idKey];
    const question = config.deleteConfirm ? config.deleteConfirm(row) : `#${id}-г устгах уу?`;
    if (!confirm(question)) return;
    try {
      await api(`${config.endpoint}/${id}`, { method: "DELETE" });
      toast(`#${id} устгагдлаа`);
      if (editing && editing[config.idKey] === id) closeForm();
      await reload();
      refreshBadges();
    } catch (ex) {
      toast(ex.message, "err");
    }
  }

  $("#add-btn").addEventListener("click", () => openForm(null));
  search.addEventListener("input", renderTable);
  if (config.toolbar) $(".toolbar-slot").appendChild(config.toolbar(reload));

  await reload();
  return { reload };
}

// =====================================================================
// 4 цэсний тохиргоо
// =====================================================================

const guideOptions = async () =>
  (await api("/guides")).map((g) => ({ value: g.guide_id, label: g.full_name }));
const tourOptions = async () =>
  (await api("/tours")).map((t) => ({ value: t.tour_id, label: `#${t.tour_id} ${t.name}` }));
const customerOptions = async () =>
  (await api("/customers")).map((c) => ({ value: c.customer_id, label: `#${c.customer_id} ${c.full_name}` }));

// ---------- Аялал ----------
function renderTours() {
  return crudPage({
    title: "Аялал",
    subtitle: "Нэмэх, засах, устгах, хайх",
    endpoint: "/tours",
    idKey: "tour_id",
    columns: [
      { key: "tour_id", label: "#" },
      { key: "name", label: "Аялал", render: (r) => `<b>${esc(r.name)}</b>` },
      { key: "destination", label: "Чиглэл" },
      { key: "days", label: "Хоног" },
      { key: "price", label: "Үнэ", render: (r) => fmtMoney(r.price) },
      { key: "guide_name", label: "Хөтөч" },
      { key: "bookings", label: "Захиалга" },
    ],
    fields: [
      { key: "name", label: "Нэр", required: true },
      { key: "destination", label: "Чиглэл", required: true },
      { key: "days", label: "Хоног", type: "number", min: 1, required: true },
      { key: "price", label: "Үнэ ₮", type: "number", min: 1, step: 1000, required: true },
      { key: "start_date", label: "Эхлэх огноо", type: "date" },
      { key: "guide_id", label: "Хөтөч", type: "select", numeric: true, options: guideOptions },
    ],
    canDelete: (r) => true,
    deleteConfirm: (r) => r.bookings > 0
      ? `«${r.name}» аялалд ${r.bookings} захиалга бий. Устгах оролдлого хийх үү?`
      : `«${r.name}» аяллыг устгах уу?`,
  });
}

// ---------- Захиалга ----------
const STATUS_LABEL = { pending: "Хүлээгдэж буй", confirmed: "Батлагдсан", cancelled: "Цуцлагдсан" };

function renderBookings() {
  let statusFilter = "";
  let page;

  async function setStatus(row, status) {
    const verb = status === "confirmed" ? "батлах" : "цуцлах";
    if (!confirm(`#${row.booking_id} захиалгыг ${verb} уу?`)) return;
    try {
      await api(`/bookings/${row.booking_id}/status`, {
        method: "PATCH", body: JSON.stringify({ status }),
      });
      toast(status === "confirmed" ? `#${row.booking_id} батлагдлаа, төлбөр бүртгэгдлээ` : `#${row.booking_id} цуцлагдлаа`);
      await page.reload();
      refreshBadges();
    } catch (ex) {
      toast(ex.message, "err");
    }
  }

  return crudPage({
    title: "Захиалга",
    subtitle: "Сүүлийн 50 захиалга. Нэмэх, засах, батлах, цуцлах, устгах",
    endpoint: "/bookings",
    idKey: "booking_id",
    listPath: () => `/bookings?limit=50${statusFilter ? `&status=${statusFilter}` : ""}`,
    columns: [
      { key: "booking_id", label: "#" },
      { key: "booked_at", label: "Огноо" },
      { key: "customer", label: "Үйлчлүүлэгч" },
      { key: "tour", label: "Аялал" },
      { key: "people", label: "Хүн" },
      { key: "status", label: "Төлөв",
        text: (r) => STATUS_LABEL[r.status],
        render: (r) => `<span class="pill ${r.status}">${STATUS_LABEL[r.status] || esc(r.status)}</span>` },
    ],
    fields: [
      { key: "tour_id", label: "Аялал", type: "select", numeric: true, required: true, options: tourOptions },
      { key: "customer_id", label: "Үйлчлүүлэгч", type: "select", numeric: true, required: true, options: customerOptions },
      { key: "people", label: "Хүний тоо", type: "number", min: 1, required: true, default: 1 },
      { key: "status", label: "Төлөв", type: "select", required: true, default: "pending",
        options: async () => Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label })) },
    ],
    toolbar: (reload) => {
      const sel = document.createElement("select");
      sel.className = "filter";
      sel.innerHTML = `<option value="">Төлөв: бүгд</option>` +
        Object.entries(STATUS_LABEL).map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
      sel.addEventListener("change", () => { statusFilter = sel.value; reload(); });
      return sel;
    },
    rowActions: (r) => r.status === "pending" ? [
      { label: "Батлах", cls: "confirm", onClick: () => setStatus(r, "confirmed") },
      { label: "Цуцлах", cls: "cancel", onClick: () => setStatus(r, "cancelled") },
    ] : [],
    // Төлбөртэй захиалгыг устгахгүй: товчийг нь харуулахгүй
    canDelete: (r) => !r.paid,
    deleteConfirm: (r) => `#${r.booking_id} захиалгыг бүр мөсөн устгах уу?`,
  }).then((p) => (page = p));
}

// ---------- Үйлчлүүлэгч ----------
function renderCustomers() {
  return crudPage({
    title: "Үйлчлүүлэгч",
    subtitle: "Нэмэх, засах, устгах, хайх",
    endpoint: "/customers",
    idKey: "customer_id",
    columns: [
      { key: "customer_id", label: "#" },
      { key: "full_name", label: "Нэр" },
      { key: "country", label: "Улс" },
      { key: "email", label: "И-мэйл" },
      { key: "bookings", label: "Захиалга" },
    ],
    fields: [
      { key: "full_name", label: "Нэр", required: true },
      { key: "country", label: "Улс" },
      { key: "email", label: "И-мэйл", type: "email" },
    ],
    deleteConfirm: (r) => `«${r.full_name}» үйлчлүүлэгчийг устгах уу?`,
  });
}

// ---------- Хөтөч ----------
function renderGuides() {
  return crudPage({
    title: "Хөтөч",
    subtitle: "Нэмэх, засах, устгах, хайх",
    endpoint: "/guides",
    idKey: "guide_id",
    columns: [
      { key: "guide_id", label: "#" },
      { key: "full_name", label: "Нэр" },
      { key: "language", label: "Хэл" },
      { key: "experience_years", label: "Туршлага (жил)" },
      { key: "tours", label: "Аялал" },
    ],
    fields: [
      { key: "full_name", label: "Нэр", required: true },
      { key: "language", label: "Хэл" },
      { key: "experience_years", label: "Туршлага (жил)", type: "number", min: 0 },
    ],
    deleteConfirm: (r) => `«${r.full_name}» хөтчийг устгах уу?`,
  });
}

// =====================================================================
// Цэс
// =====================================================================
const PAGES = {
  dashboard: renderDashboard,
  tours:     renderTours,
  bookings:  renderBookings,
  customers: renderCustomers,
  guides:    renderGuides,
  predict:   renderPredict,
  facebook:  () => renderSoon("Facebook", "Тун удахгүй"),
};

document.querySelectorAll("#menu a").forEach((a) => {
  a.addEventListener("click", () => openPage(a.dataset.page));
});

function openPage(page) {
  document.querySelectorAll("#menu a").forEach((a) =>
    a.classList.toggle("active", a.dataset.page === page));
  (PAGES[page] || renderDashboard)();
}

function renderSoon(title, label) {
  $("#content").innerHTML = `
    <h2>${esc(title)}</h2>
    <p class="sub">&nbsp;</p>
    <div class="placeholder">
      <div class="big">${esc(label)}</div>
      Энэ цэс дараагийн алхмуудад нэмэгдэнэ.
    </div>`;
}

// =====================================================================
// Эрэлт таамаглах (ML)
// =====================================================================
async function renderPredict() {
  $("#content").innerHTML = `
    <h2>Эрэлт таамаглах</h2>
    <p class="sub">Colab-д сургасан regression загвар (demand_model.joblib): үнэ, хөтчийн туршлага, хоногоос захиалгыг таамаглана</p>

    <div class="panels predict-grid">
      <form class="panel form" id="predict-form" novalidate>
        <h3>Шинэ аялал төлөвлөх</h3>
        <label>Өдрийн үнэ (₮)<input name="price_per_day" type="number" min="1" step="1000" value="180000" required></label>
        <label>Хөтчийн туршлага (жил)<input name="guide_experience" type="number" min="0" value="12" required></label>
        <label>Хоног<input name="days" type="number" min="1" value="5" required></label>
        <button class="btn orange full" type="submit">Таамаглах</button>
        <div id="predict-result" class="predict-result"></div>
      </form>

      <div class="panel">
        <h3 id="demand-title">Бодит ба таамагласан захиалга</h3>
        <div class="table-wrap" id="demand-table"><p class="empty">Ачаалж байна...</p></div>
      </div>
    </div>`;

  // ---- Форм: GET /predict-demand?price_per_day=&guide_experience=&days= ----
  const form = $("#predict-form");
  const result = $("#predict-result");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const q = new URLSearchParams({
      price_per_day: form.elements.price_per_day.value,
      guide_experience: form.elements.guide_experience.value,
      days: form.elements.days.value,
    });
    try {
      const r = await api(`/predict-demand?${q}`);
      result.textContent = `Таамаглал: ${r.predicted_bookings} захиалга`;
    } catch (ex) {
      result.textContent = "";
      toast(ex.message, "err");
    }
  });

  // ---- Хүснэгт: GET /demand/tours (хамгийн их захиалгатай 10 аялал) ----
  const wrap = $("#demand-table");
  try {
    const rows = await api("/demand/tours?limit=10");
    $("#demand-title").textContent = `Бодит ба таамагласан захиалга (${rows.length} аялал)`;
    wrap.innerHTML = rows.length ? `
      <table>
        <thead><tr>
          <th>Аялал</th><th>Хоног</th><th>Өдрийн үнэ</th><th>Хөтөч</th><th>Бодит</th><th>Таамаглал</th>
        </tr></thead>
        <tbody>${rows.map((r) => `
          <tr>
            <td>${esc(r.name)}</td>
            <td>${r.days}</td>
            <td>${fmtMoney(Math.round(r.price_per_day))}</td>
            <td>${r.guide_experience} жил</td>
            <td>${r.bookings}</td>
            <td class="pred">${r.predicted}</td>
          </tr>`).join("")}
        </tbody>
      </table>` : `<p class="empty">Мэдээлэл алга.</p>`;
  } catch (ex) {
    wrap.innerHTML = `<p class="empty">${esc(ex.message)}</p>`;
  }
}

async function refreshBadges() {
  try {
    const s = await api("/admin/stats");
    $("#badge-tours").textContent = s.total_tours;
    $("#badge-pending").textContent = s.pending_bookings;
    return s;
  } catch { return null; }
}

// ---------- Хяналтын самбар ----------
async function renderDashboard() {
  $("#content").innerHTML = `
    <h2>Хяналтын самбар</h2>
    <p class="sub">Өнөөдрийн байдал ба AI-ийн таамаглал</p>
    <p class="empty">Ачаалж байна...</p>`;

  let s;
  try {
    s = await api("/admin/stats");
  } catch (ex) {
    $(".empty", $("#content")).textContent = ex.message;
    return;
  }

  // ML: болж өнгөрсөн аяллуудаас бодит захиалгаар хамгийн их 5-ыг таамаглалтай нь харуулна
  let topHtml;
  try {
    // Backend аль хэдийн захиалгаар эрэмбэлж өгнө
    const top = await api("/demand/tours?limit=5");
    topHtml = `
      <table>
        <thead><tr><th>Аялал</th><th>Бодит</th><th>Таамаглал</th></tr></thead>
        <tbody>${top.map((t) => `
          <tr><td>${esc(t.name)}</td><td>${t.bookings}</td><td class="pred">${t.predicted}</td></tr>`).join("")}
        </tbody>
      </table>`;
  } catch (ex) {
    topHtml = `<p class="empty">${esc(ex.message)}</p>`;
  }

  $("#badge-tours").textContent = s.total_tours;
  $("#badge-pending").textContent = s.pending_bookings;

  const pendingRows = s.pending_list.length
    ? s.pending_list.map((b) => `
        <div class="pending-row">
          <span>#${b.booking_id} ${esc(b.customer)}, ${esc(b.tour)}</span>
          <span class="pill pending">Хүлээгдэж буй</span>
        </div>`).join("")
    : `<p class="empty">Батлагдаагүй захиалга алга.</p>`;

  $("#content").innerHTML = `
    <h2>Хяналтын самбар</h2>
    <p class="sub">Өнөөдрийн байдал ба AI-ийн таамаглал</p>

    <div class="cards">
      <div class="card"><div class="label">Нийт аялал</div><div class="value">${fmtNum(s.total_tours)}</div></div>
      <div class="card"><div class="label">Нийт захиалга</div><div class="value">${fmtNum(s.total_bookings)}</div></div>
      <div class="card"><div class="label">Хүлээгдэж буй</div><div class="value">${fmtNum(s.pending_bookings)}</div></div>
      <div class="card"><div class="label">Нийт орлого</div><div class="value">${fmtMoney(s.total_revenue)}</div></div>
    </div>

    <div class="panels">
      <div class="panel">
        <h3>Хамгийн эрэлттэй 5 аялал <span class="tag">ML</span></h3>
        ${topHtml}
      </div>
      <div class="panel">
        <h3>Батлагдаагүй захиалга</h3>
        ${pendingRows}
      </div>
    </div>`;
}

// ---------- Эхлэх ----------
function showApp() {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  try { $("#user-email").textContent = localStorage.getItem(EMAIL_KEY) || ""; } catch {}
  openPage("dashboard");
}

if (getToken()) showApp();
else $("#login-view").classList.remove("hidden");
