"use strict";
// All dynamic text is inserted with textContent (never innerHTML): document text is untrusted.
const $ = (id) => document.getElementById(id);
const HEADERS = { "Content-Type": "application/json", "X-Requested-With": "eka" };
let me = null;

const EXAMPLES = [
  "How many days of annual leave do I get?",
  "What is the salary band for a Staff Engineer?",
  "How many customers are in the north region?",
  "What is the discount approval policy?",
  "Draft an email to rahul.verma@northwind.example about the remote work policy",
  "Do we offer pet insurance?",
];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", headers: HEADERS, ...opts });
  if (r.status === 401) { showLogin(); throw new Error("signed out"); }
  return r;
}

function show(which) {
  for (const id of ["login", "chat", "audit"]) $(id).hidden = id !== which;
  $("tab-chat").classList.toggle("active", which === "chat");
  $("tab-audit").classList.toggle("active", which === "audit");
}

async function showLogin() {
  me = null; $("who").hidden = true; show("login");
  const cfg = await (await fetch("/api/config")).json();
  const box = $("demo-users"); box.replaceChildren();
  if (!cfg.demo_auth) { box.appendChild(el("p", "muted", "Demo sign-in is disabled. Use Google sign-in.")); return; }
  for (const u of cfg.demo_users) {
    const b = el("button", "user-btn", u.name || u.email);
    b.appendChild(el("small", null, u.email));
    b.type = "button";
    b.addEventListener("click", () => login(u.email));
    box.appendChild(b);
  }
}

async function login(email) {
  $("login-error").textContent = "";
  const r = await fetch("/api/login", { method: "POST", headers: HEADERS, credentials: "same-origin", body: JSON.stringify({ email }) });
  if (!r.ok) { $("login-error").textContent = "Sign-in failed."; return; }
  enter(await r.json());
}

function enter(user) {
  me = user; $("who").hidden = false; $("who-name").textContent = user.name || user.email;
  $("messages").replaceChildren(); show("chat");
  const ex = $("examples"); ex.replaceChildren();
  for (const t of EXAMPLES) { const b = el("button", null, t); b.type = "button"; b.addEventListener("click", () => ask(t)); ex.appendChild(b); }
}

function addMsg(cls, text) { const m = el("div", "msg " + cls, text); $("messages").appendChild(m); m.scrollIntoView({ block: "end" }); return m; }

function renderFinal(box, d) {
  const row = el("div", "status-row");
  row.appendChild(el("span", "badge " + d.status, d.status.replace("_", " ")));
  row.appendChild(el("span", "badge", d.intent));
  row.appendChild(el("span", "badge", d.latency_ms + " ms"));
  box.appendChild(row);
  if (d.sql) box.appendChild(el("div", "sqlbox", d.sql));
  if (d.columns && d.columns.length && d.rows.length > 1) {
    const t = el("table"); const h = el("tr"); for (const c of d.columns) h.appendChild(el("th", null, c)); t.appendChild(h);
    for (const r of d.rows.slice(0, 15)) { const tr = el("tr"); for (const v of r) tr.appendChild(el("td", null, String(v))); t.appendChild(tr); }
    const w = el("div", "table-wrap"); w.appendChild(t); box.appendChild(w);
  }
  if (d.citations && d.citations.length) {
    const c = el("div", "cites"); c.appendChild(el("div", "muted", "Sources"));
    for (const x of d.citations) {
      const line = el("div", "cite"); line.appendChild(el("b", null, "[" + x.label + "] "));
      line.appendChild(document.createTextNode(x.title + (x.section ? " > " + x.section : "")));
      line.appendChild(el("div", "snip", x.snippet)); c.appendChild(line);
    }
    box.appendChild(c);
  }
  if (d.approval) renderApproval(box, d.approval);
  if (d.flags && d.flags.length) box.appendChild(el("div", "flags", "Guardrails: " + d.flags.join(" | ")));
}

function renderApproval(box, a) {
  const p = a.payload, card = el("div", "approval");
  card.appendChild(el("b", null, p.kind === "email_draft" ? "Email draft (not created yet)" : "Calendar event (not created yet)"));
  card.appendChild(el("div", "small", "To: " + p.to.join(", ")));
  card.appendChild(el("div", "small", (p.kind === "email_draft" ? "Subject: " : "Title: ") + p.subject));
  if (p.start) card.appendChild(el("div", "small", "When: " + p.start + " to " + p.end));
  if (p.body) card.appendChild(el("pre", null, p.body));
  const row = el("div", "row"), ok = el("button", "primary", "Approve"), no = el("button", null, "Reject");
  ok.type = no.type = "button";
  const done = (txt) => { row.replaceChildren(el("span", "muted", txt)); };
  const decide = async (approve) => {
    ok.disabled = no.disabled = true;
    const r = await api("/api/approvals/" + a.approval_id, { method: "POST", body: JSON.stringify({ approve }) });
    const j = await r.json();
    done(j.status === "executed" ? "Approved and created: " + JSON.stringify(j.result) : j.status === "rejected" ? "Rejected." : "Could not complete: " + (j.error || JSON.stringify(j.result)));
  };
  ok.addEventListener("click", () => decide(true)); no.addEventListener("click", () => decide(false));
  row.append(ok, no); card.appendChild(row); box.appendChild(card);
}

async function ask(question) {
  $("q").value = ""; $("send").disabled = true;
  addMsg("user", question);
  const bot = addMsg("bot", ""); const text = el("div"); bot.appendChild(text);
  try {
    const r = await api("/api/chat/stream", { method: "POST", body: JSON.stringify({ question }) });
    if (!r.ok) { text.textContent = r.status === 429 ? "Too many requests. Please wait a moment." : "Something went wrong."; return; }
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(block), data = /^data: (.*)$/m.exec(block);
        if (!ev || !data) continue; const d = JSON.parse(data[1]);
        if (ev[1] === "status") text.textContent = d.message;
        else if (ev[1] === "token") { if (text.dataset.s !== "1") { text.textContent = ""; text.dataset.s = "1"; } text.textContent += d.text; }
        else if (ev[1] === "final") { text.textContent = d.answer; renderFinal(bot, d); }
        else if (ev[1] === "error") text.textContent = d.message;
      }
    }
  } catch (e) { if (String(e.message) !== "signed out") text.textContent = "Connection problem. Please try again."; }
  finally { $("send").disabled = false; bot.scrollIntoView({ block: "end" }); }
}

async function loadAudit() {
  show("audit");
  const j = await (await api("/api/audit?limit=60")).json();
  $("audit-scope").textContent = "(" + (j.scope === "all" ? "all users" : "your requests") + ")";
  const t = $("audit-table"); t.replaceChildren();
  const h = el("tr"); for (const c of ["Time", "User", "Question", "Intent", "Status", "Flags", "ms"]) h.appendChild(el("th", null, c)); t.appendChild(h);
  for (const e of j.entries) {
    const tr = el("tr");
    for (const v of [e.ts.slice(11, 19), e.user.split("@")[0], e.query, e.intent, e.answer_status, (e.flags || []).join(", "), e.latency_ms]) tr.appendChild(el("td", null, String(v ?? "")));
    t.appendChild(tr);
  }
}

$("form").addEventListener("submit", (e) => { e.preventDefault(); const q = $("q").value.trim(); if (q) ask(q); });
$("tab-chat").addEventListener("click", () => show("chat"));
$("tab-audit").addEventListener("click", loadAudit);
$("logout").addEventListener("click", async () => { await fetch("/api/logout", { method: "POST", headers: HEADERS, credentials: "same-origin" }); showLogin(); });

(async () => {
  const r = await fetch("/api/me", { credentials: "same-origin" });
  if (r.ok) enter(await r.json()); else showLogin();
})();
