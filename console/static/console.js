// Fixed14 Console: progressive enhancement only. Every page works as plain forms and links;
// this adds the image viewer, keyboard shortcuts, the re-read call and small conveniences.
(function () {
  "use strict";

  const csrf = () => (document.querySelector('input[name="csrf"]') || {}).value || "";
  const typing = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
  const ARABIC = "٠١٢٣٤٥٦٧٨٩", PERSIAN = "۰۱۲۳۴۵۶۷۸۹";
  const toAscii = (s) => s.replace(/[٠-٩۰-۹]/g, (c) => String(Math.max(ARABIC.indexOf(c), PERSIAN.indexOf(c))));

  // ---------------------------------------------------------------- small conveniences
  document.querySelectorAll("[data-autosubmit]").forEach((el) => el.addEventListener("change", () => el.form.submit()));

  document.querySelectorAll("[data-fragment]").forEach(async (box) => {
    try {
      const res = await fetch(box.dataset.fragment, { credentials: "same-origin" });
      box.innerHTML = res.ok ? await res.text() : '<p class="muted small">تعذر التحقق.</p>';
    } catch (e) {
      box.innerHTML = '<p class="muted small">تعذر التحقق.</p>';
    }
  });

  const reset = document.querySelector("[data-reset-form]");
  if (reset) {
    const who = reset.querySelector("[data-reset-user]");
    const sync = () => { reset.action = "/users/" + encodeURIComponent(who.value); };
    who.addEventListener("change", sync); sync();
  }

  document.querySelectorAll("form").forEach((f) => f.addEventListener("submit", () => {
    setTimeout(() => f.querySelectorAll('button[type="submit"]').forEach((b) => { b.disabled = true; }), 0);
  }));

  // ---------------------------------------------------------------- image viewer
  const viewer = document.querySelector("[data-viewer]");
  let view = null;
  if (viewer) {
    const img = viewer.querySelector("img");
    const msg = viewer.querySelector("[data-viewer-msg]");
    view = { scale: 1, rot: 0, x: 0, y: 0 };
    const apply = () => {
      img.style.transform = `translate(calc(-50% + ${view.x}px), calc(-50% + ${view.y}px)) rotate(${view.rot}deg) scale(${view.scale})`;
    };
    view.zoom = (f) => { view.scale = Math.min(8, Math.max(0.3, view.scale * f)); apply(); };
    view.rotate = () => { view.rot = (view.rot + 90) % 360; apply(); };
    view.fit = () => { view.scale = 1; view.x = 0; view.y = 0; apply(); };
    apply();
    img.addEventListener("error", async () => {
      img.hidden = true; msg.hidden = false;
      try { const r = await fetch(img.src, { credentials: "same-origin" }); msg.textContent = await r.text(); }
      catch (e) { msg.textContent = "تعذر تحميل الصورة."; }
    });
    viewer.addEventListener("wheel", (e) => { e.preventDefault(); view.zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
    let drag = null;
    viewer.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".viewer-tools")) return;
      drag = { x: e.clientX - view.x, y: e.clientY - view.y }; viewer.classList.add("dragging"); viewer.setPointerCapture(e.pointerId);
    });
    viewer.addEventListener("pointermove", (e) => { if (drag) { view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; apply(); } });
    const stop = () => { drag = null; viewer.classList.remove("dragging"); };
    viewer.addEventListener("pointerup", stop); viewer.addEventListener("pointercancel", stop);
    viewer.querySelector('[data-zoom="in"]').addEventListener("click", () => view.zoom(1.25));
    viewer.querySelector('[data-zoom="out"]').addEventListener("click", () => view.zoom(0.8));
    viewer.querySelector("[data-rotate]").addEventListener("click", () => view.rotate());
    viewer.querySelector("[data-fit]").addEventListener("click", () => view.fit());
  }

  // ---------------------------------------------------------------- decision form
  const form = document.querySelector("[data-decide-form]");
  const corrected = document.querySelector("[data-corrected]");
  const syncCorrected = () => {
    if (!form || !corrected) return;
    const chosen = form.querySelector('input[name="verdict"]:checked');
    const show = chosen && chosen.value === "other";
    corrected.hidden = !show;
    const input = corrected.querySelector("input");
    input.required = !!show;
    if (show) input.focus();
  };
  if (form) {
    form.addEventListener("change", (e) => { if (e.target.name === "verdict") syncCorrected(); });
    const input = corrected && corrected.querySelector("input");
    if (input) input.addEventListener("input", () => { input.value = toAscii(input.value).replace(/\D/g, "").slice(0, 14); });
  }

  // ---------------------------------------------------------------- re-read with the model
  const rereadBtn = document.querySelector("[data-reread]");
  if (rereadBtn) {
    rereadBtn.addEventListener("click", async () => {
      const box = document.querySelector("[data-reread-box]");
      rereadBtn.disabled = true; rereadBtn.textContent = "الموديل بيقرا الصورة...";
      try {
        const res = await fetch(rereadBtn.dataset.reread, { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": csrf() } });
        box.innerHTML = await res.text();
      } catch (e) {
        box.innerHTML = '<div class="notice bad">الخدمة ما ردتش.</div>';
      }
    });
  }

  // ---------------------------------------------------------------- keyboard
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const active = document.activeElement;
    if (e.key === "Enter" && !typing(active)) {
      if (form) { e.preventDefault(); form.requestSubmit(); return; }
      const start = document.querySelector('a.btn.primary[href^="/review/next"]');
      if (start) { e.preventDefault(); start.click(); }
      return;
    }
    if (typing(active)) return;
    const key = toAscii(e.key).toLowerCase();
    if (form) {
      const radio = form.querySelector(`input[name="verdict"][data-key="${key}"]`);
      if (radio && !radio.disabled) { radio.checked = true; radio.focus(); syncCorrected(); e.preventDefault(); return; }
    }
    if (view) {
      if (key === "+" || key === "=") { view.zoom(1.25); return; }
      if (key === "-") { view.zoom(0.8); return; }
      if (key === "r" || key === "ق") { view.rotate(); return; }
      if (key === "0") { view.fit(); return; }
    }
    if (key === "s" || key === "س") {
      const skip = document.querySelector('a[data-key="s"]');
      if (skip) skip.click();
    }
  });
})();
