/* ========================================================== */
/* MILTEL NOTIFICATIONS                                        */
/* Panel de notificaciones, lectura y actualización del badge  */
/* ========================================================== */

(function () {
    "use strict";

    /* ====================================================== */
    /* CONFIGURACIÓN                                          */
    /* ====================================================== */

    const PANEL_ID =
        "notification-panel";

    const NOTIFICATION_ITEM_PREFIX =
        "notificacion-";

    const READ_ENDPOINT_PREFIX =
        "/usuarios/notificacion/";

    const READ_ENDPOINT_SUFFIX =
        "/leer/";

    /* ====================================================== */
    /* ELEMENTOS                                              */
    /* ====================================================== */

    function getNotificationPanel() {
        return document.getElementById(
            PANEL_ID
        );
    }

    function getNotificationButton() {
        return document.querySelector(
            ".notification-button"
        );
    }

    function getNotificationBadge() {
        return (
            document.querySelector(
                ".notification-badge"
            ) ||
            document.querySelector(
                ".notification-button .bg-red-500"
            )
        );
    }

    function getNotificationItem(id) {
        return document.getElementById(
            `${NOTIFICATION_ITEM_PREFIX}${id}`
        );
    }

    /* ====================================================== */
    /* PANEL                                                  */
    /* ====================================================== */

    function notificationPanelIsOpen() {
        const panel =
            getNotificationPanel();

        if (!panel) {
            return false;
        }

        return !panel.classList.contains(
            "hidden"
        );
    }

    function openNotifications() {
        const panel =
            getNotificationPanel();

        if (!panel) {
            return;
        }

        panel.classList.remove(
            "hidden"
        );

        const button =
            getNotificationButton();

        if (button) {
            button.setAttribute(
                "aria-expanded",
                "true"
            );
        }
    }

    function closeNotifications() {
        const panel =
            getNotificationPanel();

        if (!panel) {
            return;
        }

        panel.classList.add(
            "hidden"
        );

        const button =
            getNotificationButton();

        if (button) {
            button.setAttribute(
                "aria-expanded",
                "false"
            );
        }
    }

    function toggleNotifications() {
        if (
            notificationPanelIsOpen()
        ) {
            closeNotifications();
            return;
        }

        openNotifications();
    }

    /* ====================================================== */
    /* BADGE                                                  */
    /* ====================================================== */

    function parseBadgeCount(
        badge
    ) {
        if (!badge) {
            return 0;
        }

        const raw =
            String(
                badge.textContent || ""
            ).trim();

        const count =
            Number.parseInt(
                raw,
                10
            );

        return Number.isFinite(count)
            ? count
            : 0;
    }

    function updateNotificationBadge(
        decrementBy = 1
    ) {
        const badge =
            getNotificationBadge();

        if (!badge) {
            return;
        }

        const currentCount =
            parseBadgeCount(
                badge
            );

        const decrement =
            Number.isFinite(
                Number(decrementBy)
            )
                ? Math.max(
                    0,
                    Number(decrementBy)
                )
                : 1;

        const nextCount =
            Math.max(
                0,
                currentCount - decrement
            );

        if (nextCount > 0) {
            badge.textContent =
                String(nextCount);

            return;
        }

        badge.remove();
    }

    /* ====================================================== */
    /* PETICIÓN DE LECTURA                                    */
    /* ====================================================== */

    function getCsrfToken() {
        const meta =
            document.querySelector(
                'meta[name="csrf-token"]'
            );

        if (
            meta &&
            meta.content
        ) {
            return meta.content;
        }

        const csrfInput =
            document.querySelector(
                'input[name="csrfmiddlewaretoken"]'
            );

        if (
            csrfInput &&
            csrfInput.value
        ) {
            return csrfInput.value;
        }

        return "";
    }

    function buildReadUrl(id) {
        return (
            `${READ_ENDPOINT_PREFIX}`
            + `${encodeURIComponent(id)}`
            + `${READ_ENDPOINT_SUFFIX}`
        );
    }

    async function markNotificationAsRead(
        id
    ) {
        const url =
            buildReadUrl(id);

        const headers = {
            "X-Requested-With":
                "XMLHttpRequest"
        };

        const csrfToken =
            getCsrfToken();

        if (csrfToken) {
            headers["X-CSRFToken"] =
                csrfToken;
        }

        const response =
            await fetch(
                url,
                {
                    method: "GET",
                    headers,
                    credentials:
                        "same-origin"
                }
            );

        if (!response.ok) {
            throw new Error(
                `No se pudo marcar la notificación ${id} como leída.`
            );
        }

        return response;
    }

    /* ====================================================== */
    /* LECTURA DE NOTIFICACIÓN                                */
    /* ====================================================== */

    async function leerNotificacion(
        id,
        urlDestino
    ) {
        if (
            id === undefined ||
            id === null ||
            id === ""
        ) {
            if (urlDestino) {
                window.location.href =
                    urlDestino;
            }

            return;
        }

        try {
            await markNotificationAsRead(
                id
            );

            const item =
                getNotificationItem(
                    id
                );

            if (item) {
                item.remove();
            }

            updateNotificationBadge(1);

            if (urlDestino) {
                window.location.href =
                    urlDestino;
            }
        } catch (error) {
            /*
             * Si el servidor no confirma la lectura, no se modifica
             * visualmente el contador. La navegación puede continuar.
             */
            if (urlDestino) {
                window.location.href =
                    urlDestino;
            }
        }
    }

    /* ====================================================== */
    /* CLIC FUERA DEL PANEL                                   */
    /* ====================================================== */

    function handleOutsideClick(
        event
    ) {
        const panel =
            getNotificationPanel();

        if (
            !panel ||
            panel.classList.contains(
                "hidden"
            )
        ) {
            return;
        }

        const button =
            event.target.closest(
                ".notification-button"
            );

        if (
            panel.contains(
                event.target
            ) ||
            button
        ) {
            return;
        }

        closeNotifications();
    }

    /* ====================================================== */
    /* TECLA ESCAPE                                           */
    /* ====================================================== */

    function handleEscapeKey(
        event
    ) {
        if (
            event.key !== "Escape"
        ) {
            return;
        }

        closeNotifications();
    }

    /* ====================================================== */
    /* ACCESIBILIDAD                                          */
    /* ====================================================== */

    function initializeNotificationButton() {
        const button =
            getNotificationButton();

        if (!button) {
            return;
        }

        button.setAttribute(
            "aria-haspopup",
            "true"
        );

        button.setAttribute(
            "aria-controls",
            PANEL_ID
        );

        button.setAttribute(
            "aria-expanded",
            notificationPanelIsOpen()
                ? "true"
                : "false"
        );
    }

    /* ====================================================== */
    /* ENLACES DE NOTIFICACIONES                              */
    /* ====================================================== */

    function initializeNotificationLinks() {
        const panel =
            getNotificationPanel();

        if (!panel) {
            return;
        }

        panel.addEventListener(
            "click",
            function (event) {
                const link =
                    event.target.closest(
                        "a[href]"
                    );

                if (!link) {
                    return;
                }

                /*
                 * En la base administrativa las notificaciones ya
                 * apuntan a la URL Django que marca como leída.
                 * No se intercepta la navegación.
                 */
                closeNotifications();
            }
        );
    }

    /* ====================================================== */
    /* INICIALIZACIÓN                                         */
    /* ====================================================== */

    function initializeNotifications() {
        initializeNotificationButton();
        initializeNotificationLinks();
    }

    /* ====================================================== */
    /* FUNCIONES GLOBALES                                     */
    /* Compatibilidad con onclick existente en las bases.     */
    /* ====================================================== */

    window.toggleNotifications =
        toggleNotifications;

    window.leerNotificacion =
        leerNotificacion;

    window.miltelOpenNotifications =
        openNotifications;

    window.miltelCloseNotifications =
        closeNotifications;

    window.miltelUpdateNotificationBadge =
        updateNotificationBadge;

    /* ====================================================== */
    /* EVENTOS                                                */
    /* ====================================================== */

    document.addEventListener(
        "DOMContentLoaded",
        initializeNotifications
    );

    document.addEventListener(
        "click",
        handleOutsideClick
    );

    document.addEventListener(
        "keydown",
        handleEscapeKey
    );
})();