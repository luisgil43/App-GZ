/* ========================================================== */
/* MILTEL UI                                                   */
/* Interacciones generales reutilizables de la plataforma      */
/* ========================================================== */

(function () {
    "use strict";

    /* ====================================================== */
    /* CONSTANTES                                             */
    /* ====================================================== */

    const HIDDEN_CLASS =
        "hidden";

    const BODY_NO_SCROLL_CLASS =
        "miltel-no-scroll";

    const DEFAULT_MESSAGE_TIMEOUT =
        5000;

    const DEFAULT_MESSAGE_FADE_DURATION =
        400;

    const DEFAULT_PASSWORD_SHOW_ICON =
        "🙈";

    const DEFAULT_PASSWORD_HIDE_ICON =
        "👁️";

    /* ====================================================== */
    /* ESTADO INTERNO                                         */
    /* ====================================================== */

    let globalEventsBound =
        false;

    let lucideObserver =
        null;

    /* ====================================================== */
    /* UTILIDADES GENERALES                                   */
    /* ====================================================== */

    function toArray(
        value
    ) {
        if (!value) {
            return [];
        }

        if (
            Array.isArray(
                value
            )
        ) {
            return value;
        }

        if (
            value instanceof NodeList ||
            value instanceof HTMLCollection
        ) {
            return Array.from(
                value
            );
        }

        return [value];
    }

    function parsePositiveInteger(
        value,
        fallback
    ) {
        const parsed =
            Number.parseInt(
                value,
                10
            );

        if (
            !Number.isFinite(
                parsed
            ) ||
            parsed < 0
        ) {
            return fallback;
        }

        return parsed;
    }

    function getElement(
        target
    ) {
        if (!target) {
            return null;
        }

        if (
            target instanceof Element
        ) {
            return target;
        }

        if (
            typeof target ===
            "string"
        ) {
            const byId =
                document.getElementById(
                    target
                );

            if (byId) {
                return byId;
            }

            try {
                return document.querySelector(
                    target
                );
            } catch (error) {
                return null;
            }
        }

        return null;
    }

    function elementIsVisible(
        element
    ) {
        if (!element) {
            return false;
        }

        if (
            element.hidden ||
            element.classList.contains(
                HIDDEN_CLASS
            )
        ) {
            return false;
        }

        return (
            window.getComputedStyle(
                element
            ).display !== "none"
        );
    }

    function setElementVisibility(
        element,
        visible
    ) {
        if (!element) {
            return;
        }

        element.hidden =
            !visible;

        element.classList.toggle(
            HIDDEN_CLASS,
            !visible
        );

        element.setAttribute(
            "aria-hidden",
            visible
                ? "false"
                : "true"
        );
    }

    /* ====================================================== */
    /* LUCIDE                                                 */
    /* ====================================================== */

    function initializeLucide(
        root = document
    ) {
        if (
            typeof window.lucide ===
                "undefined" ||
            typeof window.lucide.createIcons !==
                "function"
        ) {
            return false;
        }

        try {
            window.lucide.createIcons({
                root:
                    root instanceof Element
                        ? root
                        : document
            });

            return true;
        } catch (error) {
            /*
             * Algunas versiones de Lucide no admiten la opción
             * root. En ese caso se ejecuta de la forma clásica.
             */
            try {
                window.lucide.createIcons();
                return true;
            } catch (secondError) {
                return false;
            }
        }
    }

    function observeLucideChanges() {
        if (
            lucideObserver ||
            !document.body ||
            typeof MutationObserver ===
                "undefined"
        ) {
            return;
        }

        lucideObserver =
            new MutationObserver(
                function (
                    mutations
                ) {
                    const hasNewIcons =
                        mutations.some(
                            function (
                                mutation
                            ) {
                                return Array
                                    .from(
                                        mutation.addedNodes ||
                                            []
                                    )
                                    .some(
                                        function (
                                            node
                                        ) {
                                            if (
                                                !(
                                                    node instanceof
                                                    Element
                                                )
                                            ) {
                                                return false;
                                            }

                                            return (
                                                node.matches(
                                                    "[data-lucide]"
                                                ) ||
                                                node.querySelector(
                                                    "[data-lucide]"
                                                )
                                            );
                                        }
                                    );
                            }
                        );

                    if (hasNewIcons) {
                        initializeLucide();
                    }
                }
            );

        lucideObserver.observe(
            document.body,
            {
                childList: true,
                subtree: true
            }
        );
    }

    /* ====================================================== */
    /* MENSAJES DJANGO                                        */
    /* ====================================================== */

    function getMessageElements(
        root = document
    ) {
        return root.querySelectorAll(
            [
                "[data-miltel-message]",
                ".system-message",
                ".message-box",
                "#mensajes > div"
            ].join(",")
        );
    }

    function removeMessage(
        target,
        options = {}
    ) {
        const element =
            getElement(
                target
            );

        if (!element) {
            return;
        }

        const animated =
            options.animated !==
            false;

        const duration =
            parsePositiveInteger(
                options.duration ??
                    element.dataset.fadeDuration,
                DEFAULT_MESSAGE_FADE_DURATION
            );

        if (!animated) {
            element.remove();
            return;
        }

        element.style.transition =
            `opacity ${duration}ms ease, transform ${duration}ms ease`;

        element.style.opacity =
            "0";

        element.style.transform =
            "translateY(-6px)";

        element.style.pointerEvents =
            "none";

        window.setTimeout(
            function () {
                element.remove();

                removeEmptyMessageContainer();
            },
            duration
        );
    }

    function removeEmptyMessageContainer() {
        const container =
            document.getElementById(
                "mensajes"
            );

        if (!container) {
            return;
        }

        const remainingMessages =
            container.querySelector(
                [
                    "[data-miltel-message]",
                    ".system-message",
                    ".message-box",
                    ":scope > div"
                ].join(",")
            );

        if (!remainingMessages) {
            container.remove();
        }
    }

    function scheduleMessageRemoval(
        element
    ) {
        if (
            !element ||
            element.dataset.miltelMessageScheduled ===
                "1"
        ) {
            return;
        }

        const shouldPersist =
            element.dataset.persist ===
                "true" ||
            element.dataset.autoDismiss ===
                "false";

        if (shouldPersist) {
            return;
        }

        const timeout =
            parsePositiveInteger(
                element.dataset.timeout,
                DEFAULT_MESSAGE_TIMEOUT
            );

        element.dataset.miltelMessageScheduled =
            "1";

        window.setTimeout(
            function () {
                if (
                    document.contains(
                        element
                    )
                ) {
                    removeMessage(
                        element
                    );
                }
            },
            timeout
        );
    }

    function initializeMessages(
        root = document
    ) {
        getMessageElements(
            root
        ).forEach(
            scheduleMessageRemoval
        );
    }

    function closeMessageFromButton(
        button
    ) {
        if (!button) {
            return;
        }

        const targetSelector =
            button.dataset.messageTarget;

        let message =
            targetSelector
                ? getElement(
                    targetSelector
                )
                : null;

        if (!message) {
            message =
                button.closest(
                    [
                        "[data-miltel-message]",
                        ".system-message",
                        ".message-box",
                        "#mensajes > div"
                    ].join(",")
                );
        }

        if (message) {
            removeMessage(
                message
            );
        }
    }

    /* ====================================================== */
    /* MODALES                                                */
    /* ====================================================== */

    function getVisibleModals() {
        return Array.from(
            document.querySelectorAll(
                [
                    "[data-miltel-modal]",
                    ".miltel-modal",
                    '[id^="modal-"]'
                ].join(",")
            )
        ).filter(
            elementIsVisible
        );
    }

    function updateBodyModalState() {
        const hasVisibleModal =
            getVisibleModals().length >
            0;

        document.body.classList.toggle(
            BODY_NO_SCROLL_CLASS,
            hasVisibleModal
        );

        /*
         * Compatibilidad con el bloqueo actual basado
         * en la clase de Tailwind.
         */
        document.body.classList.toggle(
            "overflow-hidden",
            hasVisibleModal
        );
    }

    function focusModalElement(
        modal
    ) {
        if (!modal) {
            return;
        }

        const preferredSelector =
            modal.dataset.autofocus;

        let focusTarget =
            preferredSelector
                ? modal.querySelector(
                    preferredSelector
                )
                : null;

        if (!focusTarget) {
            focusTarget =
                modal.querySelector(
                    [
                        "[autofocus]",
                        "textarea:not([disabled])",
                        "input:not([type='hidden']):not([disabled])",
                        "select:not([disabled])",
                        "button:not([disabled])",
                        "a[href]"
                    ].join(",")
                );
        }

        if (
            focusTarget &&
            typeof focusTarget.focus ===
                "function"
        ) {
            window.setTimeout(
                function () {
                    focusTarget.focus({
                        preventScroll: true
                    });
                },
                30
            );
        }
    }

    function openModal(
        target
    ) {
        const modal =
            getElement(
                target
            );

        if (!modal) {
            return false;
        }

        setElementVisibility(
            modal,
            true
        );

        modal.setAttribute(
            "role",
            modal.getAttribute(
                "role"
            ) || "dialog"
        );

        modal.setAttribute(
            "aria-modal",
            "true"
        );

        updateBodyModalState();
        focusModalElement(
            modal
        );

        modal.dispatchEvent(
            new CustomEvent(
                "miltel:modal-opened",
                {
                    bubbles: true,
                    detail: {
                        modal
                    }
                }
            )
        );

        return true;
    }

    function closeModal(
        target
    ) {
        const modal =
            getElement(
                target
            );

        if (!modal) {
            return false;
        }

        setElementVisibility(
            modal,
            false
        );

        updateBodyModalState();

        modal.dispatchEvent(
            new CustomEvent(
                "miltel:modal-closed",
                {
                    bubbles: true,
                    detail: {
                        modal
                    }
                }
            )
        );

        return true;
    }

    function toggleModal(
        target
    ) {
        const modal =
            getElement(
                target
            );

        if (!modal) {
            return false;
        }

        if (
            elementIsVisible(
                modal
            )
        ) {
            return closeModal(
                modal
            );
        }

        return openModal(
            modal
        );
    }

    function closeTopModal() {
        const visibleModals =
            getVisibleModals();

        const topModal =
            visibleModals[
                visibleModals.length -
                    1
            ];

        if (topModal) {
            closeModal(
                topModal
            );
        }
    }

    function initializeModals(
        root = document
    ) {
        root.querySelectorAll(
            [
                "[data-miltel-modal]",
                ".miltel-modal"
            ].join(",")
        ).forEach(
            function (
                modal
            ) {
                modal.setAttribute(
                    "aria-hidden",
                    elementIsVisible(
                        modal
                    )
                        ? "false"
                        : "true"
                );

                if (
                    !modal.getAttribute(
                        "role"
                    )
                ) {
                    modal.setAttribute(
                        "role",
                        "dialog"
                    );
                }
            }
        );

        updateBodyModalState();
    }

    /* ====================================================== */
    /* COMPATIBILIDAD CON MODAL REABRIR                       */
    /* ====================================================== */

    function abrirModalReabrir(
        id
    ) {
        openModal(
            `modal-reabrir-${id}`
        );
    }

    function cerrarModalReabrir(
        id
    ) {
        closeModal(
            `modal-reabrir-${id}`
        );
    }

    /* ====================================================== */
    /* MOSTRAR U OCULTAR CONTRASEÑA                           */
    /* ====================================================== */

    function resolvePasswordField(
        toggle
    ) {
        if (!toggle) {
            return null;
        }

        const targetSelector =
            toggle.dataset.passwordTarget;

        if (targetSelector) {
            const explicitTarget =
                getElement(
                    targetSelector
                );

            if (
                explicitTarget &&
                explicitTarget.matches(
                    "input"
                )
            ) {
                return explicitTarget;
            }
        }

        const wrapper =
            toggle.closest(
                [
                    ".password-wrapper",
                    ".input-wrap",
                    "[data-password-wrapper]"
                ].join(",")
            );

        if (wrapper) {
            return wrapper.querySelector(
                [
                    'input[type="password"]',
                    'input[data-password-input]',
                    'input[type="text"][data-password-visible="true"]'
                ].join(",")
            );
        }

        return null;
    }

    function setPasswordVisibility(
        toggle,
        visible
    ) {
        const field =
            resolvePasswordField(
                toggle
            );

        if (!field) {
            return false;
        }

        field.type =
            visible
                ? "text"
                : "password";

        field.dataset.passwordVisible =
            visible
                ? "true"
                : "false";

        toggle.setAttribute(
            "aria-pressed",
            visible
                ? "true"
                : "false"
        );

        toggle.setAttribute(
            "aria-label",
            visible
                ? "Ocultar contraseña"
                : "Mostrar contraseña"
        );

        const showIcon =
            toggle.dataset.showIcon ||
            DEFAULT_PASSWORD_SHOW_ICON;

        const hideIcon =
            toggle.dataset.hideIcon ||
            DEFAULT_PASSWORD_HIDE_ICON;

        /*
         * Cuando el botón contiene un elemento interno para el
         * icono, solamente se actualiza ese elemento.
         */
        const iconTarget =
            toggle.querySelector(
                "[data-password-icon]"
            );

        if (iconTarget) {
            iconTarget.textContent =
                visible
                    ? showIcon
                    : hideIcon;
        } else if (
            !toggle.querySelector(
                "[data-lucide]"
            )
        ) {
            toggle.textContent =
                visible
                    ? showIcon
                    : hideIcon;
        }

        return true;
    }

    function togglePasswordVisibility(
        target
    ) {
        const toggle =
            getElement(
                target
            );

        if (!toggle) {
            return false;
        }

        const field =
            resolvePasswordField(
                toggle
            );

        if (!field) {
            return false;
        }

        const currentlyVisible =
            field.type === "text";

        return setPasswordVisibility(
            toggle,
            !currentlyVisible
        );
    }

    function initializePasswordToggles(
        root = document
    ) {
        root.querySelectorAll(
            [
                "[data-password-toggle]",
                ".toggle-password"
            ].join(",")
        ).forEach(
            function (
                toggle
            ) {
                toggle.setAttribute(
                    "role",
                    toggle.getAttribute(
                        "role"
                    ) || "button"
                );

                if (
                    !toggle.hasAttribute(
                        "tabindex"
                    )
                ) {
                    toggle.setAttribute(
                        "tabindex",
                        "0"
                    );
                }

                const field =
                    resolvePasswordField(
                        toggle
                    );

                const isVisible =
                    field?.type ===
                    "text";

                toggle.setAttribute(
                    "aria-pressed",
                    isVisible
                        ? "true"
                        : "false"
                );

                toggle.setAttribute(
                    "aria-label",
                    isVisible
                        ? "Ocultar contraseña"
                        : "Mostrar contraseña"
                );
            }
        );
    }

    /* ====================================================== */
    /* PREVENIR ENVÍOS DOBLES                                 */
    /* ====================================================== */

    function disableSubmitButtons(
        form
    ) {
        if (!form) {
            return;
        }

        form.querySelectorAll(
            [
                'button[type="submit"]',
                'input[type="submit"]'
            ].join(",")
        ).forEach(
            function (
                button
            ) {
                if (
                    button.dataset.keepEnabled ===
                    "true"
                ) {
                    return;
                }

                button.disabled =
                    true;

                button.setAttribute(
                    "aria-disabled",
                    "true"
                );

                button.classList.add(
                    "is-loading"
                );

                const loadingText =
                    button.dataset.loadingText;

                if (
                    loadingText &&
                    !button.dataset.originalText
                ) {
                    button.dataset.originalText =
                        button instanceof
                        HTMLInputElement
                            ? button.value
                            : button.innerHTML;

                    if (
                        button instanceof
                        HTMLInputElement
                    ) {
                        button.value =
                            loadingText;
                    } else {
                        button.textContent =
                            loadingText;
                    }
                }
            }
        );
    }

    function enableSubmitButtons(
        form
    ) {
        if (!form) {
            return;
        }

        form.querySelectorAll(
            [
                'button[type="submit"]',
                'input[type="submit"]'
            ].join(",")
        ).forEach(
            function (
                button
            ) {
                button.disabled =
                    false;

                button.removeAttribute(
                    "aria-disabled"
                );

                button.classList.remove(
                    "is-loading"
                );

                if (
                    button.dataset.originalText
                ) {
                    if (
                        button instanceof
                        HTMLInputElement
                    ) {
                        button.value =
                            button.dataset.originalText;
                    } else {
                        button.innerHTML =
                            button.dataset.originalText;
                    }

                    delete button.dataset
                        .originalText;
                }
            }
        );

        delete form.dataset
            .miltelSubmitting;
    }

    function handleFormSubmit(
        event
    ) {
        const form =
            event.target;

        if (
            !(
                form instanceof
                HTMLFormElement
            )
        ) {
            return;
        }

        if (
            form.dataset.preventDoubleSubmit ===
            "false"
        ) {
            return;
        }

        if (
            form.dataset.miltelSubmitting ===
            "1"
        ) {
            event.preventDefault();
            return;
        }

        /*
         * No bloqueamos botones si la validación nativa
         * indica que el formulario es inválido.
         */
        if (
            typeof form.checkValidity ===
                "function" &&
            !form.checkValidity()
        ) {
            return;
        }

        form.dataset.miltelSubmitting =
            "1";

        window.setTimeout(
            function () {
                disableSubmitButtons(
                    form
                );
            },
            0
        );
    }

    /* ====================================================== */
    /* EVENTOS DE CLIC                                        */
    /* ====================================================== */

    function handleDocumentClick(
        event
    ) {
        const messageCloseButton =
            event.target.closest(
                [
                    "[data-miltel-message-close]",
                    ".message-close"
                ].join(",")
            );

        if (messageCloseButton) {
            event.preventDefault();

            closeMessageFromButton(
                messageCloseButton
            );

            return;
        }

        const modalOpenButton =
            event.target.closest(
                "[data-miltel-modal-open]"
            );

        if (modalOpenButton) {
            event.preventDefault();

            openModal(
                modalOpenButton.dataset
                    .miltelModalOpen
            );

            return;
        }

        const modalCloseButton =
            event.target.closest(
                "[data-miltel-modal-close]"
            );

        if (modalCloseButton) {
            event.preventDefault();

            const target =
                modalCloseButton.dataset
                    .miltelModalClose;

            if (target) {
                closeModal(
                    target
                );
            } else {
                closeModal(
                    modalCloseButton.closest(
                        [
                            "[data-miltel-modal]",
                            ".miltel-modal",
                            '[id^="modal-"]'
                        ].join(",")
                    )
                );
            }

            return;
        }

        const passwordToggle =
            event.target.closest(
                [
                    "[data-password-toggle]",
                    ".toggle-password"
                ].join(",")
            );

        if (passwordToggle) {
            event.preventDefault();

            togglePasswordVisibility(
                passwordToggle
            );

            return;
        }

        /*
         * Cierre del modal al pulsar directamente sobre
         * su fondo. No se cierra al pulsar el contenido.
         */
        const visibleModal =
            event.target.closest(
                [
                    "[data-miltel-modal]",
                    ".miltel-modal",
                    '[id^="modal-"]'
                ].join(",")
            );

        if (
            visibleModal &&
            event.target ===
                visibleModal &&
            visibleModal.dataset
                .closeOnBackdrop !==
                "false"
        ) {
            closeModal(
                visibleModal
            );
        }
    }

    /* ====================================================== */
    /* EVENTOS DE TECLADO                                     */
    /* ====================================================== */

    function handleDocumentKeydown(
        event
    ) {
        if (
            event.key ===
            "Escape"
        ) {
            closeTopModal();
            return;
        }

        if (
            event.key !==
                "Enter" &&
            event.key !==
                " "
        ) {
            return;
        }

        const passwordToggle =
            event.target.closest(
                [
                    "[data-password-toggle]",
                    ".toggle-password"
                ].join(",")
            );

        if (!passwordToggle) {
            return;
        }

        event.preventDefault();

        togglePasswordVisibility(
            passwordToggle
        );
    }

    /* ====================================================== */
    /* OBSERVAR CONTENIDO INSERTADO DINÁMICAMENTE             */
    /* ====================================================== */

    function observeDynamicContent() {
        if (
            !document.body ||
            typeof MutationObserver ===
                "undefined"
        ) {
            return;
        }

        const observer =
            new MutationObserver(
                function (
                    mutations
                ) {
                    mutations.forEach(
                        function (
                            mutation
                        ) {
                            toArray(
                                mutation.addedNodes
                            ).forEach(
                                function (
                                    node
                                ) {
                                    if (
                                        !(
                                            node instanceof
                                            Element
                                        )
                                    ) {
                                        return;
                                    }

                                    initializeMessages(
                                        node
                                    );

                                    initializePasswordToggles(
                                        node
                                    );

                                    initializeModals(
                                        node
                                    );
                                }
                            );
                        }
                    );
                }
            );

        observer.observe(
            document.body,
            {
                childList: true,
                subtree: true
            }
        );
    }

    /* ====================================================== */
    /* EVENTOS GLOBALES                                       */
    /* ====================================================== */

    function bindGlobalEvents() {
        if (globalEventsBound) {
            return;
        }

        globalEventsBound =
            true;

        document.addEventListener(
            "click",
            handleDocumentClick
        );

        document.addEventListener(
            "keydown",
            handleDocumentKeydown
        );

        document.addEventListener(
            "submit",
            handleFormSubmit
        );

        /*
         * Al volver desde el historial del navegador se
         * rehabilitan botones que pudieron quedar bloqueados.
         */
        window.addEventListener(
            "pageshow",
            function (
                event
            ) {
                if (
                    !event.persisted
                ) {
                    return;
                }

                document
                    .querySelectorAll(
                        "form[data-miltel-submitting='1']"
                    )
                    .forEach(
                        enableSubmitButtons
                    );
            }
        );
    }

    /* ====================================================== */
    /* INICIALIZACIÓN                                         */
    /* ====================================================== */

    function initializeUi() {
        initializeLucide();
        initializeMessages();
        initializeModals();
        initializePasswordToggles();

        observeLucideChanges();
        observeDynamicContent();
    }

    /* ====================================================== */
    /* API GLOBAL                                             */
    /* ====================================================== */

    window.miltelInitializeLucide =
        initializeLucide;

    window.miltelInitializeMessages =
        initializeMessages;

    window.miltelRemoveMessage =
        removeMessage;

    window.miltelOpenModal =
        openModal;

    window.miltelCloseModal =
        closeModal;

    window.miltelToggleModal =
        toggleModal;

    window.miltelCloseTopModal =
        closeTopModal;

    window.miltelTogglePassword =
        togglePasswordVisibility;

    window.miltelDisableSubmitButtons =
        disableSubmitButtons;

    window.miltelEnableSubmitButtons =
        enableSubmitButtons;

    /*
     * Compatibilidad con las funciones usadas actualmente
     * en los templates de servicios.
     */
    window.abrirModalReabrir =
        abrirModalReabrir;

    window.cerrarModalReabrir =
        cerrarModalReabrir;

    /* ====================================================== */
    /* ARRANQUE                                               */
    /* ====================================================== */

    bindGlobalEvents();

    if (
        document.readyState ===
        "loading"
    ) {
        document.addEventListener(
            "DOMContentLoaded",
            initializeUi
        );
    } else {
        initializeUi();
    }
})();