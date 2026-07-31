/* ========================================================== */
/* MILTEL SCROLL MEMORY                                        */
/* Conserva y restaura la posición de páginas y contenedores   */
/* ========================================================== */

(function () {
    "use strict";

    /* ====================================================== */
    /* CONFIGURACIÓN                                          */
    /* ====================================================== */

    const STORAGE_PREFIX =
        "miltel-scroll:";

    const DEFAULT_ZONE_SELECTOR =
        ".overflow-x-auto";

    const DEFAULT_RESTORE_DELAY =
        0;

    const SECOND_RESTORE_DELAY =
        80;

    const THIRD_RESTORE_DELAY =
        250;

    const MAX_STORED_STATES =
        50;

    /* ====================================================== */
    /* ESTADO INTERNO                                         */
    /* ====================================================== */

    const registeredContexts =
        new Map();

    let globalEventsBound =
        false;

    /* ====================================================== */
    /* UTILIDADES                                             */
    /* ====================================================== */

    function safeSessionGet(
        key
    ) {
        try {
            return sessionStorage.getItem(
                key
            );
        } catch (error) {
            return null;
        }
    }

    function safeSessionSet(
        key,
        value
    ) {
        try {
            sessionStorage.setItem(
                key,
                value
            );

            return true;
        } catch (error) {
            return false;
        }
    }

    function safeSessionRemove(
        key
    ) {
        try {
            sessionStorage.removeItem(
                key
            );

            return true;
        } catch (error) {
            return false;
        }
    }

    function getElement(
        target
    ) {
        if (!target) {
            return null;
        }

        if (
            target instanceof
            Element
        ) {
            return target;
        }

        if (
            typeof target ===
            "string"
        ) {
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

    function normalizeContextName(
        value
    ) {
        return String(
            value ||
            "default"
        )
            .trim()
            .replace(
                /\s+/g,
                "-"
            )
            .replace(
                /[^a-zA-Z0-9:_-]/g,
                "-"
            );
    }

    function buildStorageKey(
        contextName
    ) {
        const normalizedName =
            normalizeContextName(
                contextName
            );

        return (
            STORAGE_PREFIX +
            normalizedName +
            ":" +
            window.location.pathname
        );
    }

    function getWindowScrollX() {
        return (
            window.scrollX ||
            window.pageXOffset ||
            document.documentElement.scrollLeft ||
            document.body.scrollLeft ||
            0
        );
    }

    function getWindowScrollY() {
        return (
            window.scrollY ||
            window.pageYOffset ||
            document.documentElement.scrollTop ||
            document.body.scrollTop ||
            0
        );
    }

    function parseStoredState(
        raw
    ) {
        if (!raw) {
            return null;
        }

        try {
            const parsed =
                JSON.parse(
                    raw
                );

            if (
                !parsed ||
                typeof parsed !==
                    "object"
            ) {
                return null;
            }

            return parsed;
        } catch (error) {
            return null;
        }
    }

    function numberOrZero(
        value
    ) {
        const parsed =
            Number(
                value
            );

        return Number.isFinite(
            parsed
        )
            ? parsed
            : 0;
    }

    /* ====================================================== */
    /* CONTEXTO                                               */
    /* ====================================================== */

    function createContext(
        name,
        options = {}
    ) {
        const normalizedName =
            normalizeContextName(
                name
            );

        const existing =
            registeredContexts.get(
                normalizedName
            );

        if (existing) {
            Object.assign(
                existing,
                options
            );

            return existing;
        }

        const context = {
            name:
                normalizedName,

            storageKey:
                options.storageKey ||
                buildStorageKey(
                    normalizedName
                ),

            zoneSelector:
                options.zoneSelector ||
                DEFAULT_ZONE_SELECTOR,

            zone:
                options.zone ||
                null,

            saveWindow:
                options.saveWindow !==
                false,

            saveZone:
                options.saveZone !==
                false,

            restoreWindow:
                options.restoreWindow !==
                false,

            restoreZone:
                options.restoreZone !==
                false,

            clearAfterRestore:
                options.clearAfterRestore ===
                true,

            restoreAttempts:
                options.restoreAttempts ||
                3,

            rememberQuery:
                options.rememberQuery ===
                true,

            lastSavedAt:
                0
        };

        registeredContexts.set(
            normalizedName,
            context
        );

        return context;
    }

    function resolveContext(
        contextOrName
    ) {
        if (
            contextOrName &&
            typeof contextOrName ===
                "object" &&
            contextOrName.name
        ) {
            return contextOrName;
        }

        const normalizedName =
            normalizeContextName(
                contextOrName ||
                "default"
            );

        return (
            registeredContexts.get(
                normalizedName
            ) ||
            createContext(
                normalizedName
            )
        );
    }

    function resolveZone(
        context
    ) {
        if (!context) {
            return null;
        }

        if (
            context.zone instanceof
            Element &&
            document.contains(
                context.zone
            )
        ) {
            return context.zone;
        }

        if (
            typeof context.zone ===
            "string"
        ) {
            const zoneFromString =
                getElement(
                    context.zone
                );

            if (zoneFromString) {
                context.zone =
                    zoneFromString;

                return zoneFromString;
            }
        }

        const selector =
            context.zoneSelector ||
            DEFAULT_ZONE_SELECTOR;

        const zone =
            getElement(
                selector
            );

        if (zone) {
            context.zone =
                zone;
        }

        return zone;
    }

    /* ====================================================== */
    /* GUARDAR ESTADO                                         */
    /* ====================================================== */

    function buildCurrentState(
        context
    ) {
        const zone =
            resolveZone(
                context
            );

        const state = {
            version: 1,

            pathname:
                window.location.pathname,

            savedAt:
                Date.now(),

            winX:
                context.saveWindow
                    ? getWindowScrollX()
                    : 0,

            winY:
                context.saveWindow
                    ? getWindowScrollY()
                    : 0,

            zoneX:
                context.saveZone &&
                zone
                    ? zone.scrollLeft
                    : 0,

            zoneY:
                context.saveZone &&
                zone
                    ? zone.scrollTop
                    : 0
        };

        if (
            context.rememberQuery
        ) {
            state.search =
                window.location.search;
        }

        return state;
    }

    function saveScroll(
        contextOrName = "default"
    ) {
        const context =
            resolveContext(
                contextOrName
            );

        const state =
            buildCurrentState(
                context
            );

        const saved =
            safeSessionSet(
                context.storageKey,
                JSON.stringify(
                    state
                )
            );

        if (saved) {
            context.lastSavedAt =
                state.savedAt;
        }

        cleanOldStates();

        return state;
    }

    /* ====================================================== */
    /* LEER ESTADO                                            */
    /* ====================================================== */

    function getSavedScroll(
        contextOrName = "default"
    ) {
        const context =
            resolveContext(
                contextOrName
            );

        const raw =
            safeSessionGet(
                context.storageKey
            );

        const state =
            parseStoredState(
                raw
            );

        if (!state) {
            return null;
        }

        if (
            state.pathname &&
            state.pathname !==
                window.location.pathname
        ) {
            return null;
        }

        if (
            context.rememberQuery &&
            state.search &&
            state.search !==
                window.location.search
        ) {
            return null;
        }

        return state;
    }

    /* ====================================================== */
    /* RESTAURAR ESTADO                                       */
    /* ====================================================== */

    function applySavedState(
        context,
        state
    ) {
        if (
            !context ||
            !state
        ) {
            return false;
        }

        if (
            context.restoreWindow
        ) {
            window.scrollTo(
                numberOrZero(
                    state.winX
                ),
                numberOrZero(
                    state.winY
                )
            );
        }

        if (
            context.restoreZone
        ) {
            const zone =
                resolveZone(
                    context
                );

            if (zone) {
                zone.scrollLeft =
                    numberOrZero(
                        state.zoneX
                    );

                zone.scrollTop =
                    numberOrZero(
                        state.zoneY
                    );
            }
        }

        return true;
    }

    function restoreScroll(
        contextOrName = "default",
        options = {}
    ) {
        const context =
            resolveContext(
                contextOrName
            );

        const state =
            options.state ||
            getSavedScroll(
                context
            );

        if (!state) {
            return false;
        }

        const attempts =
            Number.isFinite(
                Number(
                    options.attempts
                )
            )
                ? Number(
                    options.attempts
                )
                : context.restoreAttempts;

        const delays = [
            DEFAULT_RESTORE_DELAY,
            SECOND_RESTORE_DELAY,
            THIRD_RESTORE_DELAY
        ];

        const totalAttempts =
            Math.max(
                1,
                attempts
            );

        for (
            let index = 0;
            index < totalAttempts;
            index += 1
        ) {
            const delay =
                delays[index] ??
                (
                    THIRD_RESTORE_DELAY +
                    index * 150
                );

            window.setTimeout(
                function () {
                    window.requestAnimationFrame(
                        function () {
                            applySavedState(
                                context,
                                state
                            );
                        }
                    );
                },
                delay
            );
        }

        if (
            options.clearAfterRestore ===
                true ||
            context.clearAfterRestore
        ) {
            window.setTimeout(
                function () {
                    clearScroll(
                        context
                    );
                },
                delays[
                    Math.min(
                        totalAttempts - 1,
                        delays.length - 1
                    )
                ] +
                    100
            );
        }

        return true;
    }

    /* ====================================================== */
    /* LIMPIAR ESTADO                                         */
    /* ====================================================== */

    function clearScroll(
        contextOrName = "default"
    ) {
        const context =
            resolveContext(
                contextOrName
            );

        return safeSessionRemove(
            context.storageKey
        );
    }

    function clearAllScrollStates() {
        try {
            const keys = [];

            for (
                let index = 0;
                index <
                sessionStorage.length;
                index += 1
            ) {
                const key =
                    sessionStorage.key(
                        index
                    );

                if (
                    key &&
                    key.startsWith(
                        STORAGE_PREFIX
                    )
                ) {
                    keys.push(
                        key
                    );
                }
            }

            keys.forEach(
                function (
                    key
                ) {
                    sessionStorage.removeItem(
                        key
                    );
                }
            );

            return true;
        } catch (error) {
            return false;
        }
    }

    /* ====================================================== */
    /* LIMPIEZA DE ESTADOS ANTIGUOS                           */
    /* ====================================================== */

    function cleanOldStates() {
        try {
            const storedStates = [];

            for (
                let index = 0;
                index <
                sessionStorage.length;
                index += 1
            ) {
                const key =
                    sessionStorage.key(
                        index
                    );

                if (
                    !key ||
                    !key.startsWith(
                        STORAGE_PREFIX
                    )
                ) {
                    continue;
                }

                const state =
                    parseStoredState(
                        sessionStorage.getItem(
                            key
                        )
                    );

                storedStates.push({
                    key,
                    savedAt:
                        numberOrZero(
                            state?.savedAt
                        )
                });
            }

            if (
                storedStates.length <=
                MAX_STORED_STATES
            ) {
                return;
            }

            storedStates.sort(
                function (
                    first,
                    second
                ) {
                    return (
                        first.savedAt -
                        second.savedAt
                    );
                }
            );

            const amountToRemove =
                storedStates.length -
                MAX_STORED_STATES;

            storedStates
                .slice(
                    0,
                    amountToRemove
                )
                .forEach(
                    function (
                        item
                    ) {
                        sessionStorage.removeItem(
                            item.key
                        );
                    }
                );
        } catch (error) {
            // No interrumpe la navegación.
        }
    }

    /* ====================================================== */
    /* REGISTRO DE CONTEXTOS                                  */
    /* ====================================================== */

    function registerScrollMemory(
        name,
        options = {}
    ) {
        const context =
            createContext(
                name,
                options
            );

        if (
            options.restoreOnRegister !==
            false
        ) {
            restoreScroll(
                context
            );
        }

        return context;
    }

    function unregisterScrollMemory(
        name,
        options = {}
    ) {
        const normalizedName =
            normalizeContextName(
                name
            );

        const context =
            registeredContexts.get(
                normalizedName
            );

        if (
            context &&
            options.clear ===
                true
        ) {
            clearScroll(
                context
            );
        }

        return registeredContexts.delete(
            normalizedName
        );
    }

    /* ====================================================== */
    /* DETECTAR CONTEXTO DE UN ELEMENTO                       */
    /* ====================================================== */

    function findContextForElement(
        element
    ) {
        if (!element) {
            return null;
        }

        const explicitContext =
            element.closest(
                "[data-scroll-memory-context]"
            );

        if (
            explicitContext &&
            explicitContext.dataset
                .scrollMemoryContext
        ) {
            return resolveContext(
                explicitContext.dataset
                    .scrollMemoryContext
            );
        }

        const contexts =
            Array.from(
                registeredContexts.values()
            );

        for (
            const context of
            contexts
        ) {
            const zone =
                resolveZone(
                    context
                );

            if (
                zone &&
                (
                    zone === element ||
                    zone.contains(
                        element
                    )
                )
            ) {
                return context;
            }
        }

        return (
            contexts[0] ||
            resolveContext(
                "default"
            )
        );
    }

    /* ====================================================== */
    /* GUARDAR TODOS LOS CONTEXTOS                            */
    /* ====================================================== */

    function saveAllContexts() {
        if (
            registeredContexts.size ===
            0
        ) {
            saveScroll(
                "default"
            );

            return;
        }

        registeredContexts.forEach(
            function (
                context
            ) {
                saveScroll(
                    context
                );
            }
        );
    }

    function restoreAllContexts() {
        if (
            registeredContexts.size ===
            0
        ) {
            restoreScroll(
                "default"
            );

            return;
        }

        registeredContexts.forEach(
            function (
                context
            ) {
                restoreScroll(
                    context
                );
            }
        );
    }

    /* ====================================================== */
    /* EVENTOS DE NAVEGACIÓN                                  */
    /* ====================================================== */

    function shouldSaveForLink(
        link
    ) {
        if (!link) {
            return false;
        }

        if (
            link.dataset.noScrollMemory ===
            "true"
        ) {
            return false;
        }

        const href =
            link.getAttribute(
                "href"
            );

        if (
            !href ||
            href.startsWith(
                "#"
            ) ||
            href.startsWith(
                "javascript:"
            ) ||
            link.target ===
                "_blank" ||
            link.hasAttribute(
                "download"
            )
        ) {
            return false;
        }

        return true;
    }

    function handleDocumentClick(
        event
    ) {
        const link =
            event.target.closest(
                "a[href]"
            );

        if (
            !link ||
            !shouldSaveForLink(
                link
            )
        ) {
            return;
        }

        const context =
            findContextForElement(
                link
            );

        saveScroll(
            context
        );
    }

    function handleDocumentSubmit(
        event
    ) {
        const form =
            event.target;

        if (
            !(
                form instanceof
                HTMLFormElement
            ) ||
            form.dataset.noScrollMemory ===
                "true"
        ) {
            return;
        }

        const context =
            findContextForElement(
                form
            );

        saveScroll(
            context
        );
    }

    function handleBeforeUnload() {
        saveAllContexts();
    }

    function handlePageShow(
        event
    ) {
        if (
            event.persisted
        ) {
            restoreAllContexts();
        }
    }

    /* ====================================================== */
    /* EVENTOS AJAX                                           */
    /* ====================================================== */

    function beforeAjaxReplace(
        contextOrName = "default"
    ) {
        return saveScroll(
            contextOrName
        );
    }

    function afterAjaxReplace(
        contextOrName = "default",
        options = {}
    ) {
        const context =
            resolveContext(
                contextOrName
            );

        /*
         * Se elimina la referencia anterior para que vuelva
         * a localizar el nuevo contenedor insertado por AJAX.
         */
        context.zone =
            options.zone ||
            null;

        if (
            options.zoneSelector
        ) {
            context.zoneSelector =
                options.zoneSelector;
        }

        return restoreScroll(
            context,
            options
        );
    }

    /* ====================================================== */
    /* INICIALIZACIÓN AUTOMÁTICA POR ATRIBUTOS                */
    /* ====================================================== */

    function initializeDeclaredContexts() {
        const declared =
            document.querySelectorAll(
                "[data-scroll-memory-context]"
            );

        declared.forEach(
            function (
                element
            ) {
                const name =
                    element.dataset
                        .scrollMemoryContext;

                if (!name) {
                    return;
                }

                const zoneSelector =
                    element.dataset
                        .scrollMemoryZone;

                registerScrollMemory(
                    name,
                    {
                        zone:
                            zoneSelector
                                ? null
                                : element,

                        zoneSelector:
                            zoneSelector ||
                            undefined,

                        saveWindow:
                            element.dataset
                                .scrollMemoryWindow !==
                            "false",

                        saveZone:
                            element.dataset
                                .scrollMemoryContainer !==
                            "false",

                        rememberQuery:
                            element.dataset
                                .scrollMemoryQuery ===
                            "true"
                    }
                );
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
            "submit",
            handleDocumentSubmit
        );

        window.addEventListener(
            "beforeunload",
            handleBeforeUnload
        );

        window.addEventListener(
            "pageshow",
            handlePageShow
        );
    }

    /* ====================================================== */
    /* COMPATIBILIDAD CON VISTAS ACTUALES                     */
    /* ====================================================== */

    function registerCompatibilityContexts() {
        /*
         * Servicios Cotizados - Supervisor
         */
        if (
            document.getElementById(
                "servicios-supervisor-table"
            ) ||
            document.getElementById(
                "zonaTabla"
            )
        ) {
            registerScrollMemory(
                "servicios-supervisor",
                {
                    zoneSelector:
                        "#zonaTabla .overflow-x-auto"
                }
            );
        }

        /*
         * Mis servicios asignados del técnico.
         */
        if (
            document.querySelector(
                "#excel-filter-form"
            ) ||
            document.querySelector(
                "[data-scroll-view='mis-servicios-tecnico']"
            )
        ) {
            registerScrollMemory(
                "mis-servicios-tecnico",
                {
                    zoneSelector:
                        ".overflow-x-auto"
                }
            );
        }
    }

    function serviciosSupervisorSaveScroll() {
        return saveScroll(
            "servicios-supervisor"
        );
    }

    function serviciosSupervisorRestoreScroll() {
        return restoreScroll(
            "servicios-supervisor"
        );
    }

    function misServiciosTecnicoSaveScroll() {
        return saveScroll(
            "mis-servicios-tecnico"
        );
    }

    function misServiciosTecnicoRestoreScroll() {
        return restoreScroll(
            "mis-servicios-tecnico"
        );
    }

    /* ====================================================== */
    /* API GLOBAL                                             */
    /* ====================================================== */

    window.miltelRegisterScrollMemory =
        registerScrollMemory;

    window.miltelUnregisterScrollMemory =
        unregisterScrollMemory;

    window.miltelSaveScroll =
        saveScroll;

    window.miltelRestoreScroll =
        restoreScroll;

    window.miltelClearScroll =
        clearScroll;

    window.miltelClearAllScrollStates =
        clearAllScrollStates;

    window.miltelBeforeAjaxReplace =
        beforeAjaxReplace;

    window.miltelAfterAjaxReplace =
        afterAjaxReplace;

    window.miltelSaveAllScrollContexts =
        saveAllContexts;

    window.miltelRestoreAllScrollContexts =
        restoreAllContexts;

    /*
     * Compatibilidad con las funciones que actualmente
     * están usadas en la plantilla del supervisor.
     */
    window.serviciosSupervisorSaveScroll =
        serviciosSupervisorSaveScroll;

    window.serviciosSupervisorRestoreScroll =
        serviciosSupervisorRestoreScroll;

    window.misServiciosTecnicoSaveScroll =
        misServiciosTecnicoSaveScroll;

    window.misServiciosTecnicoRestoreScroll =
        misServiciosTecnicoRestoreScroll;

    /* ====================================================== */
    /* ARRANQUE                                               */
    /* ====================================================== */

    bindGlobalEvents();

    function initializeScrollMemory() {
        initializeDeclaredContexts();
        registerCompatibilityContexts();
    }

    if (
        document.readyState ===
        "loading"
    ) {
        document.addEventListener(
            "DOMContentLoaded",
            initializeScrollMemory
        );
    } else {
        initializeScrollMemory();
    }
})();