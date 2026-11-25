(() => {
  const form = document.getElementById("gz-session-activity");
  if (!form) return;

  const token = form.elements.csrfmiddlewaretoken.value;
  let pending = false;
  let sending = false;
  let nextSend = 0;
  let timer;

  async function sendActivity() {
    if (sending || !pending) return;

    const delay = nextSend - performance.now();
    if (delay > 0) {
      timer ??= window.setTimeout(() => {
        timer = undefined;
        sendActivity();
      }, delay);
      return;
    }

    pending = false;
    sending = true;
    nextSend = performance.now() + 1000;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: {"X-CSRFToken": token},
        keepalive: true,
      });
      if (response.redirected) window.location.assign(response.url);
    } catch {
      // La próxima interacción vuelve a intentar registrar actividad.
    } finally {
      sending = false;
      sendActivity();
    }
  }

  function recordActivity(event) {
    if (!event.isTrusted || document.visibilityState !== "visible") return;
    pending = true;
    sendActivity();
  }

  for (const eventType of ["pointerdown", "click", "keydown", "input", "wheel", "touchmove"]) {
    window.addEventListener(eventType, recordActivity, {capture: true, passive: true});
  }
})();
