(() => {
  const solicitud = document.getElementById("solicitud-entregable");
  if (solicitud) {
    const titulo = solicitud.elements.titulo;
    const nombres = document.getElementById("nombres-requerimientos");
    const extensiones = Array.from(solicitud.querySelectorAll('input[name="extensiones"]'));
    const resumen = document.getElementById("resumen-extensiones-entregable");
    const selectorNombres = document.getElementById("selector-nombres-requerimientos");
    const abrirNombres = document.getElementById("abrir-nombres-requerimientos");
    const listaRequisitos = document.getElementById("lista-requerimientos-entregables");
    const estadoRequisitos = document.getElementById("estado-requerimientos-entregables");
    let consulta;

    function mostrarExtensiones() {
      const activas = extensiones.filter(campo => campo.checked).map(campo => campo.value.toUpperCase());
      resumen.textContent = activas.join(", ") || "Cualquier formato documental";
    }

    function cerrarNombres() {
      consulta?.abort();
      nombres.hidden = true;
      abrirNombres.setAttribute("aria-expanded", "false");
    }

    function seleccionarNombre(perfil) {
      titulo.value = perfil.titulo;
      solicitud.elements.instrucciones.value = perfil.instrucciones;
      solicitud.elements.obligatorio.checked = perfil.obligatorio;
      solicitud.elements.formato.value = perfil.formato ?? "";
      solicitud.elements.ejemplo.value = "";
      extensiones.forEach(campo => { campo.checked = perfil.extensiones.includes(campo.value); });
      mostrarExtensiones();
      cerrarNombres();
      titulo.focus();
    }

    function mostrarMensajeNombres(texto) {
      const mensaje = document.createElement("p");
      mensaje.className = "px-2 py-1 text-sm text-slate-600";
      mensaje.textContent = texto;
      nombres.replaceChildren(mensaje);
    }

    async function buscarNombres(texto, enfocar = false) {
      consulta?.abort();
      const actual = new AbortController();
      consulta = actual;
      nombres.hidden = false;
      abrirNombres.setAttribute("aria-expanded", "true");
      mostrarMensajeNombres("Buscando…");
      const url = new URL(solicitud.dataset.sugerenciasUrl, window.location.origin);
      url.searchParams.set("q", texto);
      try {
        const respuesta = await fetch(url, {signal: actual.signal, headers: {"Accept": "application/json"}});
        if (!respuesta.ok || respuesta.redirected) throw new Error();
        const datos = await respuesta.json();
        if (actual.signal.aborted) return;
        if (!datos.resultados.length) {
          mostrarMensajeNombres("No hay nombres guardados que coincidan.");
          return;
        }
        nombres.replaceChildren(...datos.resultados.map(perfil => {
          const opcion = document.createElement("button");
          opcion.type = "button";
          opcion.className = "block w-full rounded px-2 py-1.5 text-left text-sm text-slate-800 hover:bg-sky-50 focus:bg-sky-50";
          opcion.textContent = perfil.titulo;
          opcion.addEventListener("click", () => seleccionarNombre(perfil));
          return opcion;
        }));
        if (enfocar) nombres.querySelector("button")?.focus();
      } catch {
        if (!actual.signal.aborted) mostrarMensajeNombres("No se pudieron cargar los nombres guardados.");
      }
    }

    titulo.setAttribute("autocomplete", "off");
    titulo.setAttribute("aria-controls", nombres.id);
    titulo.addEventListener("input", () => buscarNombres(titulo.value));
    abrirNombres.addEventListener("click", () => {
      if (nombres.hidden) buscarNombres("", true);
      else cerrarNombres();
    });
    selectorNombres.addEventListener("keydown", evento => {
      if (evento.key === "Escape") {
        cerrarNombres();
        titulo.focus();
      } else if (evento.key === "ArrowDown") {
        evento.preventDefault();
        if (nombres.hidden) buscarNombres(titulo.value, true);
        else (evento.target.nextElementSibling?.matches("button") && nombres.contains(evento.target)
          ? evento.target.nextElementSibling : nombres.querySelector("button"))?.focus();
      } else if (evento.key === "ArrowUp" && nombres.contains(evento.target)) {
        evento.preventDefault();
        (evento.target.previousElementSibling || titulo).focus();
      }
    });
    selectorNombres.addEventListener("focusout", evento => {
      if (!selectorNombres.contains(evento.relatedTarget)) cerrarNombres();
    });
    document.addEventListener("click", evento => {
      if (!selectorNombres.contains(evento.target)) cerrarNombres();
    });

    listaRequisitos.addEventListener("click", async evento => {
      const boton = evento.target.closest("[data-quitar-requisito]");
      if (!boton || boton.disabled) return;
      boton.disabled = true;
      estadoRequisitos.textContent = "Quitando requerimiento…";
      try {
        const respuesta = await fetch(boton.dataset.quitarRequisito, {
          method: "POST",
          headers: {"Accept": "application/json", "X-CSRFToken": solicitud.elements.csrfmiddlewaretoken.value},
        });
        const datos = await respuesta.json().catch(() => ({}));
        if (!respuesta.ok || !datos.ok) throw new Error();
        boton.closest("li").remove();
        const restantes = listaRequisitos.querySelectorAll("[data-quitar-requisito]");
        document.getElementById("requerimientos-entregables-vacio").hidden = restantes.length > 0;
        document.getElementById("conteo-requerimientos-entregables").textContent =
          `${restantes.length} solicitado${restantes.length === 1 ? "" : "s"}`;
        estadoRequisitos.textContent = "Requerimiento eliminado.";
        (restantes[0] || titulo).focus({preventScroll: true});
      } catch {
        estadoRequisitos.textContent = "No se pudo quitar el requerimiento. Inténtalo nuevamente.";
        boton.disabled = false;
      }
    });
    extensiones.forEach(campo => campo.addEventListener("change", mostrarExtensiones));
    mostrarExtensiones();
  }

  function transferir(formulario, campos, mostrarProgreso, mostrarGuardado) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", formulario.action);
      xhr.responseType = "json";
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", evento => {
        if (evento.lengthComputable) {
          mostrarProgreso(Math.floor(100 * evento.loaded / evento.total));
        }
      });
      xhr.upload.addEventListener("load", mostrarGuardado);
      xhr.onload = () => {
        if (xhr.status === 201 && xhr.response?.id) {
          resolve(xhr.response);
        } else {
          reject(new Error(xhr.response?.error || `No se pudo adjuntar el archivo (HTTP ${xhr.status}).`));
        }
      };
      xhr.onerror = () => reject(new Error("Se interrumpió la conexión. Inténtalo nuevamente."));
      xhr.send(campos);
    });
  }

  document.getElementById("entregables-tecnico")?.addEventListener("submit", async evento => {
    const formulario = evento.target.closest("[data-form-entrega], [data-quitar-entrega]");
    if (!formulario) return;
    evento.preventDefault();
    const boton = formulario.querySelector('button[type="submit"]');
    if (boton.disabled) return;

    const campos = new FormData(formulario);
    const tarjeta = formulario.closest("[data-entrega-tarjeta]");
    const mensaje = tarjeta.querySelector("[data-estado-entrega]");
    const eliminando = formulario.matches("[data-quitar-entrega]");
    const archivo = formulario.elements.archivo;
    boton.disabled = true;
    if (archivo) archivo.disabled = true;
    mensaje.textContent = eliminando ? "Eliminando archivo…" : "Preparando subida…";
    try {
      let resultado;
      if (eliminando) {
        const respuesta = await fetch(formulario.action, {
          method: "POST", body: campos, headers: {"Accept": "application/json"},
        });
        resultado = await respuesta.json().catch(() => ({}));
        if (!respuesta.ok || !resultado.ok) {
          throw new Error(resultado.error || "No se pudo eliminar el archivo.");
        }
      } else {
        resultado = await transferir(formulario, campos,
          porcentaje => { mensaje.textContent = `Subiendo archivo… ${porcentaje}%`; },
          () => { mensaje.textContent = "Guardando archivo en Wasabi…"; });
      }

      tarjeta.insertAdjacentHTML("afterend", resultado.fila);
      const nueva = tarjeta.nextElementSibling;
      nueva.querySelector("[data-estado-entrega]").textContent = eliminando
        ? "Archivo eliminado. Ya puedes adjuntar otro."
        : "Archivo adjuntado.";
      tarjeta.remove();
      nueva.querySelector(eliminando ? 'input[type="file"]' : "a, summary")?.focus({preventScroll: true});
    } catch (error) {
      mensaje.textContent = error.message;
      boton.disabled = false;
      if (archivo) archivo.disabled = false;
    }
  });

  const form = document.getElementById("form-entregable");
  if (!form) return;

  const lista = document.getElementById("lista-entregables");
  const listaVacia = document.getElementById("entregables-vacio");
  const estadoEntregables = document.getElementById("entregables-estado");
  const dialogo = document.getElementById("entregables");
  const verEntregables = document.getElementById("ver-entregables");
  const cerrarEntregables = document.getElementById("cerrar-entregables");
  verEntregables.addEventListener("click", () => dialogo.showModal());
  cerrarEntregables.addEventListener("click", () => dialogo.close());
  if (window.location.hash === "#entregables") dialogo.showModal();
  dialogo.addEventListener("close", () => {
    estadoEntregables.textContent = "";
    if (window.location.hash === "#entregables") {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
    verEntregables.focus({preventScroll: true});
  });
  const archivo = document.getElementById("entregable-archivo");
  const progreso = document.getElementById("entregable-progreso");
  const estado = document.getElementById("entregable-estado");
  const espera = document.getElementById("entregable-espera");
  const botonSubir = form.querySelector('button[type="submit"]');
  const selector = form.elements.requerimiento;
  const formatosLibres = archivo.accept;

  function actualizarSelector() {
    const vinculado = Boolean(selector.value);
    form.elements.titulo.disabled = vinculado;
    form.elements.titulo.required = !vinculado;
    form.elements.titulo.placeholder = vinculado ? "Se usa el nombre del requerimiento" : "Ej. Informe de cálculo";
    const extension = selector.selectedOptions[0]?.dataset.extension;
    archivo.accept = extension || formatosLibres;
  }

  function actualizarRevision(id, entregado) {
    if (!id) return;
    const requisito = document.querySelector(`[data-revision-requisito="${id}"]`);
    const opcion = Array.from(selector.options).find(elemento => elemento.value === String(id));
    if (!requisito || !opcion) return;
    opcion.disabled = entregado;
    requisito.querySelector("[data-revision-pendiente]").hidden = entregado;
    requisito.querySelector("[data-revision-abrir]").hidden = !entregado;
    const pendientes = document.querySelectorAll(
      '[data-obligatorio="si"] [data-revision-pendiente]:not([hidden])',
    ).length;
    document.getElementById("entregables-pendientes").textContent = `${pendientes} obligatorios pendientes`;
    const aprobar = document.getElementById("aprobar-revision");
    if (aprobar) aprobar.disabled = pendientes > 0;
  }

  selector.addEventListener("change", actualizarSelector);
  actualizarSelector();
  document.querySelectorAll("[data-revision-abrir]").forEach(boton => {
    boton.addEventListener("click", () => {
      dialogo.showModal();
      lista.querySelector(`[data-entrega-requisito="${boton.dataset.revisionAbrir}"]`)?.scrollIntoView({block: "nearest"});
    });
  });

  form.addEventListener("submit", async evento => {
    evento.preventDefault();
    if (botonSubir.disabled) return;

    if (!archivo.files.length) return;
    const campos = new FormData(form);

    botonSubir.disabled = true;
    archivo.disabled = true;
    selector.disabled = true;
    form.elements.titulo.disabled = true;
    espera.hidden = false;
    progreso.hidden = false;
    progreso.removeAttribute("value");
    try {
      estado.textContent = "Preparando subida…";
      const resultado = await transferir(form, campos, porcentaje => {
        progreso.value = porcentaje;
        estado.textContent = `Subiendo archivo… ${porcentaje}%`;
      }, () => {
        progreso.value = 100;
        estado.textContent = "Guardando archivo en Wasabi…";
      });
      listaVacia.hidden = true;
      if (!document.getElementById(`entregable-${resultado.id}`)) {
        lista.insertAdjacentHTML("afterbegin", resultado.fila);
      }
      form.reset();
      actualizarRevision(resultado.requerimiento, true);
      estado.textContent = "Entregable adjuntado.";
    } catch (error) {
      estado.textContent = error.message;
    } finally {
      espera.hidden = true;
      progreso.hidden = true;
      botonSubir.disabled = false;
      archivo.disabled = false;
      selector.disabled = false;
      actualizarSelector();
    }
  });

  lista.addEventListener("click", async evento => {
    const boton = evento.target.closest("[data-pedir-eliminar], [data-cancelar-eliminar], [data-eliminar-url]");
    if (!boton || boton.disabled) return;

    const fila = boton.closest("li");
    const panel = fila.querySelector("[data-confirmacion-eliminar]");
    const abrir = fila.querySelector("[data-pedir-eliminar]");
    const cancelar = fila.querySelector("[data-cancelar-eliminar]");
    const confirmar = fila.querySelector("[data-eliminar-url]");
    const mensaje = fila.querySelector("[data-eliminar-estado]");
    if (boton !== confirmar) {
      panel.hidden = boton === cancelar;
      abrir.setAttribute("aria-expanded", String(!panel.hidden));
      mensaje.textContent = "";
      (panel.hidden ? abrir : cancelar).focus();
      return;
    }

    abrir.disabled = cancelar.disabled = confirmar.disabled = true;
    mensaje.textContent = "Eliminando archivo…";
    try {
      const respuesta = await fetch(confirmar.dataset.eliminarUrl, {
        method: "POST",
        headers: {"Accept": "application/json", "X-CSRFToken": form.elements.csrfmiddlewaretoken.value},
      });
      const datos = await respuesta.json().catch(() => ({}));
      if (!respuesta.ok || !datos.ok) {
        throw new Error(datos.error || "No se pudo eliminar el archivo. Inténtalo nuevamente.");
      }
      const siguiente = fila.nextElementSibling?.querySelector("[data-pedir-eliminar]")
        || fila.previousElementSibling?.querySelector("[data-pedir-eliminar]");
      fila.remove();
      actualizarRevision(fila.dataset.entregaRequisito, false);
      listaVacia.hidden = Boolean(lista.querySelector("[data-eliminar-url]"));
      if (dialogo.open) {
        estadoEntregables.textContent = "Entregable eliminado.";
        (siguiente || cerrarEntregables).focus({preventScroll: true});
      }
    } catch (error) {
      mensaje.textContent = error.message;
      abrir.disabled = cancelar.disabled = confirmar.disabled = false;
      if (dialogo.open) confirmar.focus({preventScroll: true});
    }
  });

  lista.addEventListener("click", async evento => {
    const boton = evento.target.closest("[data-descarga-url]");
    if (!boton || boton.disabled) return;

    const fila = boton.closest("li");
    const indicador = fila.querySelector("[data-descarga-espera]");
    const mensaje = fila.querySelector("[data-descarga-estado]");
    boton.disabled = true;
    indicador.hidden = false;
    mensaje.textContent = "Preparando descarga…";
    try {
      const respuesta = await fetch(boton.dataset.descargaUrl, {
        headers: {"Accept": "application/json"},
      });
      const datos = await respuesta.json().catch(() => ({}));
      if (!respuesta.ok || !datos.url) {
        throw new Error(datos.error || "No se pudo preparar la descarga. Revisa tu sesión e inténtalo nuevamente.");
      }
      const enlace = document.createElement("a");
      enlace.href = datos.url;
      enlace.referrerPolicy = "no-referrer";
      document.body.append(enlace);
      enlace.click();
      enlace.remove();
      mensaje.textContent = "Descarga iniciada. Sigue el avance en Descargas del navegador.";
    } catch (error) {
      mensaje.textContent = error.message;
    } finally {
      indicador.hidden = true;
      boton.disabled = false;
    }
  });
})();
