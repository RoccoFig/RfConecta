/* RF — interacciones de los paneles. Sin dependencias: menú móvil, diálogos, confirmaciones, filtros y avisos. */
(() => {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const cuerpo = document.body;

  // Menú lateral en pantallas chicas
  const cerrarMenu = () => cuerpo.classList.remove("menu-abierto");
  $$("[data-menu]").forEach(b => b.addEventListener("click", () => cuerpo.classList.toggle("menu-abierto")));
  cuerpo.addEventListener("click", e => { if (cuerpo.classList.contains("menu-abierto") && !e.target.closest(".lateral, [data-menu]")) cerrarMenu(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") cerrarMenu(); });

  // Diálogos: abrir con data-abrir="id", cerrar con data-cerrar o haciendo clic afuera
  $$("[data-abrir]").forEach(b => b.addEventListener("click", () => {
    const d = document.getElementById(b.dataset.abrir); if (!d) return;
    (b.dataset.valores ? Object.entries(JSON.parse(b.dataset.valores)) : []).forEach(([k, v]) => { const c = d.querySelector(`[name="${k}"]`); if (c) c.value = v; });
    if (b.dataset.accion) d.querySelector("form").action = b.dataset.accion;
    d.showModal(); const p = d.querySelector("[autofocus], input:not([type=hidden]), select"); if (p) p.focus();
  }));
  $$("dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d || e.target.closest("[data-cerrar]")) d.close(); }));

  // Confirmación antes de acciones importantes: <form data-confirmar="¿Seguro?">
  const conf = $("#confirmar");
  if (conf) {
    let pendiente = null;
    document.addEventListener("submit", e => {
      const f = e.target; if (!f.dataset.confirmar || f.dataset.ok) return;
      e.preventDefault(); pendiente = f;
      $("#confirmar-texto").textContent = f.dataset.confirmar;
      $("#confirmar-si").textContent = f.dataset.boton || "Confirmar";
      conf.showModal();
    });
    $("#confirmar-si").addEventListener("click", () => { conf.close(); if (pendiente) { pendiente.dataset.ok = "1"; pendiente.requestSubmit(); } });
  }

  // Filtro instantáneo de tablas chicas: <input data-filtrar="#tabla"> y <select data-filtrar="#tabla" data-col="2">
  $$("[data-filtrar]").forEach(ctl => {
    const tabla = $(ctl.dataset.filtrar); if (!tabla) return;
    const aplicar = () => {
      const filtros = $$("[data-filtrar='" + ctl.dataset.filtrar + "']");
      let visibles = 0;
      $$("tbody tr", tabla).forEach(tr => {
        const ok = filtros.every(f => {
          const v = f.value.trim().toLowerCase(); if (!v) return true;
          return f.dataset.col ? (tr.dataset[f.dataset.col] || "").toLowerCase() === v : tr.textContent.toLowerCase().includes(v);
        });
        tr.classList.toggle("oculto", !ok); if (ok) visibles++;
      });
      const v = $("#sin-resultados"); if (v) v.classList.toggle("oculto", visibles > 0);
    };
    ctl.addEventListener("input", aplicar); ctl.addEventListener("change", aplicar);
  });
  // Los filtros del servidor se envían solos al cambiar un selector
  $$("form[data-auto] select").forEach(s => s.addEventListener("change", () => s.form.requestSubmit()));

  // Avisos: desaparecen solos
  $$(".toast").forEach(t => setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = 0; setTimeout(() => t.remove(), 300); }, 5200));

  // Imprimir (página del QR para imprimir)
  $$("[data-imprimir]").forEach(b => b.addEventListener("click", () => window.print()));

  // Editor de diseño: la vista previa es la pantalla real del cliente, armada por el servidor con los valores sin guardar
  const editor = $("[data-tema-editor]"), marco = $("[data-vista-marco]");
  if (editor && marco) {
    const caja = $("[data-vista-caja]");
    let pagina = "inicio", espera;
    const valores = () => {
      const p = new URLSearchParams();
      new FormData(editor).forEach((v, k) => { if (typeof v === "string" && k !== "csrf") p.append(k, v); });
      return p;
    };
    const recargar = () => { marco.src = marco.dataset.base.replace("__p__", pagina) + "?" + valores(); };
    const cambio = e => {
      if (e.target.type === "file") return;
      $("[data-sin-guardar]").hidden = false;
      clearTimeout(espera); espera = setTimeout(recargar, 300);
    };
    editor.addEventListener("input", cambio);
    editor.addEventListener("change", cambio);
    const elegir = (sel, b) => $$(sel).forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    $$("[data-vista-pagina]").forEach(b => b.addEventListener("click", () => { pagina = b.dataset.vistaPagina; elegir("[data-vista-pagina]", b); recargar(); }));
    // modo computadora: la página se arma a 1280 px de ancho y se achica para entrar en el recuadro
    const pantalla = caja.querySelector(".telefono");
    new ResizeObserver(() => caja.style.setProperty("--escala", pantalla.clientWidth / 1280)).observe(pantalla);
    $$("[data-vista-equipo]").forEach(b => b.addEventListener("click", () => {
      caja.classList.toggle("escritorio", b.dataset.vistaEquipo === "escritorio"); elegir("[data-vista-equipo]", b);
    }));
    editor.addEventListener("submit", () => {
      const b = editor.querySelector("[data-guardar]");
      setTimeout(() => { b.disabled = true; b.textContent = "Guardando…"; });  // después del envío, para no cancelarlo
    });
  }

  // Copiar al portapapeles
  $$("[data-copiar]").forEach(b => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copiar); const t = b.textContent; b.textContent = "¡Copiado!"; setTimeout(() => b.textContent = t, 1400); } catch { /* sin permiso */ }
  }));
})();
