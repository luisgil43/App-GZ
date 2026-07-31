/* ========================================================== */
/* MILTEL LAYOUT                                               */
/* Sidebar, submenús, header y comportamiento responsive       */
/* ========================================================== */

(function () {
    "use strict";

    /* ====================================================== */
    /* CONSTANTES                                             */
    /* ====================================================== */

    const DESKTOP_MEDIA_QUERY =
        "(min-width: 768px)";

    const DEFAULT_SIDEBAR_WIDTH =
        "18rem";

    const DEFAULT_ADMIN_STORAGE_KEY =
        "admin-sidebar-collapsed";

    const DEFAULT_USER_STORAGE_KEY =
        "user-sidebar-collapsed";

    const DEFAULT_ADMIN_INIT_CLASS =
        "admin-sidebar-collapsed-init";

    const DEFAULT_USER_INIT_CLASS =
        "user-sidebar-collapsed-init";

    const MOBILE_HIDDEN_CLASS =
        "-translate-x-full";

    const COLLAPSED_CLASS =
        "collapsed";

    const WITH_SIDEBAR_CLASS =
        "with-sidebar";

    const NO_SIDEBAR_CLASS =
        "no-sidebar";

    const SUBMENU_HIDDEN_CLASS =
        "hidden";

    const ARROW_ROTATED_CLASS =
        "rotate-90";

    /* ====================================================== */
    /* ESTADO INTERNO                                         */
    /* ====================================================== */

    let resizeFrame = null;

    let outsideClickBound = false;

    /* ====================================================== */
    /* ELEMENTOS PRINCIPALES                                  */
    /* ====================================================== */

    function getHtmlElement() {
        return document.documentElement;
    }

    function getSidebar() {
        return document.getElementById(
            "side-nav"
        );
    }

    function getAppWrapper() {
        return document.getElementById(
            "app-wrapper"
        );
    }

    function getContentWrapper() {
        return (
            document.querySelector(
                ".content-wrapper"
            ) ||
            getAppWrapper()
        );
    }

    function getMainContent() {
        return document.getElementById(
            "main-content"
        );
    }

    function getHeader() {
        return (
            document.getElementById(
                "admin-header"
            ) ||
            document.getElementById(
                "user-header"
            ) ||
            document.querySelector(
                "[data-miltel-header]"
            ) ||
            document.querySelector(
                "body > header"
            )
        );
    }

    function getMenuButton() {
        return (
            document.querySelector(
                "[data-miltel-sidebar-toggle]"
            ) ||
            document.querySelector(
                ".admin-menu-button"
            ) ||
            document.querySelector(
                ".user-menu-button"
            )
        );
    }

    /* ====================================================== */
    /* TIPO DE LAYOUT                                         */
    /* ====================================================== */

    function getLayoutMode() {
        const body =
            document.body;

        const wrapper =
            getContentWrapper();

        const explicitMode =
            body?.dataset?.miltelLayout ||
            wrapper?.dataset?.miltelLayout ||
            "";

        if (
            explicitMode === "admin" ||
            explicitMode === "user"
        ) {
            return explicitMode;
        }

        if (
            document.getElementById(
                "admin-header"
            ) ||
            document.querySelector(
                ".admin-menu-button"
            )
        ) {
            return "admin";
        }

        return "user";
    }

    function getSidebarStorageKey() {
        const sidebar =
            getSidebar();

        const wrapper =
            getContentWrapper();

        const explicitKey =
            sidebar?.dataset?.storageKey ||
            wrapper?.dataset?.sidebarStorageKey ||
            document.body?.dataset?.sidebarStorageKey;

        if (explicitKey) {
            return explicitKey;
        }

        return getLayoutMode() === "admin"
            ? DEFAULT_ADMIN_STORAGE_KEY
            : DEFAULT_USER_STORAGE_KEY;
    }

    function getInitialCollapsedClass() {
        const sidebar =
            getSidebar();

        const wrapper =
            getContentWrapper();

        const explicitClass =
            sidebar?.dataset?.initialCollapsedClass ||
            wrapper?.dataset?.initialCollapsedClass ||
            document.body?.dataset?.initialCollapsedClass;

        if (explicitClass) {
            return explicitClass;
        }

        return getLayoutMode() === "admin"
            ? DEFAULT_ADMIN_INIT_CLASS
            : DEFAULT_USER_INIT_CLASS;
    }

    /* ====================================================== */
    /* MEDIA QUERY                                            */
    /* ====================================================== */

    function isDesktop() {
        return window
            .matchMedia(
                DESKTOP_MEDIA_QUERY
            )
            .matches;
    }

    /* ====================================================== */
    /* LOCAL STORAGE                                          */
    /* ====================================================== */

    function readStorage(
        key
    ) {
        try {
            return localStorage.getItem(
                key
            );
        } catch (error) {
            return null;
        }
    }

    function writeStorage(
        key,
        value
    ) {
        try {
            localStorage.setItem(
                key,
                value
            );

            return true;
        } catch (error) {
            return false;
        }
    }

    function getSavedCollapsedState() {
        return (
            readStorage(
                getSidebarStorageKey()
            ) === "1"
        );
    }

    function saveCollapsedState(
        collapsed
    ) {
        writeStorage(
            getSidebarStorageKey(),
            collapsed
                ? "1"
                : "0"
        );
    }

    /* ====================================================== */
    /* ATRIBUTOS DE ACCESIBILIDAD                             */
    /* ====================================================== */

    function updateSidebarAccessibility(
        isOpen
    ) {
        const sidebar =
            getSidebar();

        const button =
            getMenuButton();

        if (sidebar) {
            sidebar.setAttribute(
                "aria-hidden",
                isOpen
                    ? "false"
                    : "true"
            );
        }

        if (button) {
            button.setAttribute(
                "aria-expanded",
                isOpen
                    ? "true"
                    : "false"
            );

            if (
                sidebar &&
                !sidebar.id
            ) {
                sidebar.id =
                    "side-nav";
            }

            if (sidebar?.id) {
                button.setAttribute(
                    "aria-controls",
                    sidebar.id
                );
            }
        }
    }

    function sidebarIsOpen() {
        const sidebar =
            getSidebar();

        if (!sidebar) {
            return false;
        }

        if (isDesktop()) {
            return !sidebar.classList.contains(
                COLLAPSED_CLASS
            );
        }

        return !sidebar.classList.contains(
            MOBILE_HIDDEN_CLASS
        );
    }

    /* ====================================================== */
    /* ESTADO DEL SIDEBAR                                     */
    /* ====================================================== */

    function applyDesktopSidebarState(
        collapsed
    ) {
        const sidebar =
            getSidebar();

        const wrapper =
            getContentWrapper();

        if (
            !sidebar ||
            !wrapper
        ) {
            return;
        }

        sidebar.classList.toggle(
            COLLAPSED_CLASS,
            collapsed
        );

        /*
         * En escritorio Tailwind añade md:translate-x-0.
         * Retiramos la clase móvil para evitar conflicto con
         * el estado guardado.
         */
        sidebar.classList.remove(
            MOBILE_HIDDEN_CLASS
        );

        wrapper.classList.toggle(
            NO_SIDEBAR_CLASS,
            collapsed
        );

        wrapper.classList.toggle(
            WITH_SIDEBAR_CLASS,
            !collapsed
        );

        updateSidebarAccessibility(
            !collapsed
        );
    }

    function applyMobileSidebarState(
        open = false
    ) {
        const sidebar =
            getSidebar();

        const wrapper =
            getContentWrapper();

        if (
            !sidebar ||
            !wrapper
        ) {
            return;
        }

        sidebar.classList.remove(
            COLLAPSED_CLASS
        );

        sidebar.classList.toggle(
            MOBILE_HIDDEN_CLASS,
            !open
        );

        /*
         * En móvil el contenido nunca debe conservar
         * margen lateral.
         */
        wrapper.classList.remove(
            NO_SIDEBAR_CLASS
        );

        wrapper.classList.add(
            WITH_SIDEBAR_CLASS
        );

        updateSidebarAccessibility(
            open
        );
    }

    function removeInitialCollapsedClasses() {
        const html =
            getHtmlElement();

        html.classList.remove(
            DEFAULT_ADMIN_INIT_CLASS,
            DEFAULT_USER_INIT_CLASS,
            getInitialCollapsedClass()
        );
    }

    function applySidebarState() {
        const sidebar =
            getSidebar();

        const wrapper =
            getContentWrapper();

        if (
            !sidebar ||
            !wrapper
        ) {
            removeInitialCollapsedClasses();
            return;
        }

        if (isDesktop()) {
            applyDesktopSidebarState(
                getSavedCollapsedState()
            );
        } else {
            applyMobileSidebarState(
                false
            );
        }

        removeInitialCollapsedClasses();
    }

    function toggleSidebar() {
        const sidebar =
            getSidebar();

        const wrapper =
            getContentWrapper();

        if (
            !sidebar ||
            !wrapper
        ) {
            return;
        }

        if (isDesktop()) {
            const willCollapse =
                !sidebar.classList.contains(
                    COLLAPSED_CLASS
                );

            applyDesktopSidebarState(
                willCollapse
            );

            saveCollapsedState(
                willCollapse
            );

            return;
        }

        const willOpen =
            sidebar.classList.contains(
                MOBILE_HIDDEN_CLASS
            );

        applyMobileSidebarState(
            willOpen
        );
    }

    function openSidebar() {
        if (isDesktop()) {
            applyDesktopSidebarState(
                false
            );

            saveCollapsedState(
                false
            );

            return;
        }

        applyMobileSidebarState(
            true
        );
    }

    function closeSidebar() {
        if (isDesktop()) {
            applyDesktopSidebarState(
                true
            );

            saveCollapsedState(
                true
            );

            return;
        }

        applyMobileSidebarState(
            false
        );
    }

    function closeMobileSidebar() {
        if (isDesktop()) {
            return;
        }

        applyMobileSidebarState(
            false
        );
    }

    /* ====================================================== */
    /* SUBMENÚS                                               */
    /* ====================================================== */

    function getSubmenuFromTrigger(
        trigger
    ) {
        if (!trigger) {
            return null;
        }

        const controlledId =
            trigger.getAttribute(
                "aria-controls"
            );

        if (controlledId) {
            const controlledElement =
                document.getElementById(
                    controlledId
                );

            if (
                controlledElement &&
                controlledElement.classList.contains(
                    "submenu"
                )
            ) {
                return controlledElement;
            }
        }

        const nextElement =
            trigger.nextElementSibling;

        if (
            nextElement &&
            nextElement.classList.contains(
                "submenu"
            )
        ) {
            return nextElement;
        }

        return null;
    }

    function getSubmenuArrow(
        trigger
    ) {
        return trigger?.querySelector(
            ".flecha"
        ) || null;
    }

    function submenuIsOpen(
        submenu
    ) {
        if (!submenu) {
            return false;
        }

        const hiddenByClass =
            submenu.classList.contains(
                SUBMENU_HIDDEN_CLASS
            );

        const hiddenByStyle =
            submenu.style.display ===
            "none";

        return !(
            hiddenByClass ||
            hiddenByStyle
        );
    }

    function setSubmenuState(
        trigger,
        open
    ) {
        const submenu =
            getSubmenuFromTrigger(
                trigger
            );

        if (!submenu) {
            return;
        }

        const arrow =
            getSubmenuArrow(
                trigger
            );

        submenu.classList.toggle(
            SUBMENU_HIDDEN_CLASS,
            !open
        );

        /*
         * Se conserva style.display por compatibilidad con
         * el comportamiento actual de las dos bases.
         */
        submenu.style.display =
            open
                ? "block"
                : "none";

        if (arrow) {
            arrow.classList.toggle(
                ARROW_ROTATED_CLASS,
                open
            );
        }

        trigger.setAttribute(
            "aria-expanded",
            open
                ? "true"
                : "false"
        );

        if (!submenu.id) {
            const generatedId =
                `miltel-submenu-${Math.random()
                    .toString(36)
                    .slice(2, 10)}`;

            submenu.id =
                generatedId;
        }

        trigger.setAttribute(
            "aria-controls",
            submenu.id
        );
    }

    function toggleMenu(
        trigger
    ) {
        const submenu =
            getSubmenuFromTrigger(
                trigger
            );

        if (!submenu) {
            return;
        }

        setSubmenuState(
            trigger,
            !submenuIsOpen(
                submenu
            )
        );
    }

    function initializeSubmenus() {
        const sidebar =
            getSidebar();

        if (!sidebar) {
            return;
        }

        const submenuTriggers =
            sidebar.querySelectorAll(
                [
                    "[data-miltel-submenu-toggle]",
                    "button + .submenu",
                    "div + .submenu"
                ].join(",")
            );

        /*
         * Los selectores button + .submenu y div + .submenu
         * devuelven el submenu. En ese caso obtenemos el
         * elemento inmediatamente anterior.
         */
        const triggers =
            new Set();

        submenuTriggers.forEach(
            function (element) {
                if (
                    element.classList.contains(
                        "submenu"
                    )
                ) {
                    if (
                        element.previousElementSibling
                    ) {
                        triggers.add(
                            element.previousElementSibling
                        );
                    }

                    return;
                }

                triggers.add(
                    element
                );
            }
        );

        triggers.forEach(
            function (trigger) {
                const submenu =
                    getSubmenuFromTrigger(
                        trigger
                    );

                if (!submenu) {
                    return;
                }

                if (!submenu.id) {
                    submenu.id =
                        `miltel-submenu-${Math.random()
                            .toString(36)
                            .slice(2, 10)}`;
                }

                trigger.setAttribute(
                    "aria-controls",
                    submenu.id
                );

                trigger.setAttribute(
                    "aria-expanded",
                    submenuIsOpen(
                        submenu
                    )
                        ? "true"
                        : "false"
                );
            }
        );
    }

    /* ====================================================== */
    /* ALTURA DEL HEADER                                      */
    /* ====================================================== */

    function applyHeaderOffset() {
        const header =
            getHeader();

        const wrapper =
            getAppWrapper();

        const sidebar =
            getSidebar();

        if (
            !header ||
            !wrapper
        ) {
            return;
        }

        const headerHeight =
            Math.ceil(
                header
                    .getBoundingClientRect()
                    .height || 0
            );

        if (
            !Number.isFinite(
                headerHeight
            ) ||
            headerHeight <= 0
        ) {
            return;
        }

        wrapper.style.paddingTop =
            `${headerHeight}px`;

        document.documentElement.style.setProperty(
            "--miltel-header-height",
            `${headerHeight}px`
        );

        if (sidebar) {
            sidebar.style.top =
                `${headerHeight}px`;

            sidebar.style.height =
                `calc(100vh - ${headerHeight}px)`;
        }
    }

    function requestLayoutUpdate() {
        if (resizeFrame) {
            window.cancelAnimationFrame(
                resizeFrame
            );
        }

        resizeFrame =
            window.requestAnimationFrame(
                function () {
                    applySidebarState();
                    applyHeaderOffset();

                    resizeFrame =
                        null;
                }
            );
    }

    /* ====================================================== */
    /* EVENTOS DEL SIDEBAR MÓVIL                              */
    /* ====================================================== */

    function handleMobileLinkClick(
        event
    ) {
        if (isDesktop()) {
            return;
        }

        const sidebar =
            getSidebar();

        if (!sidebar) {
            return;
        }

        const link =
            event.target.closest(
                "#side-nav a[href]"
            );

        if (!link) {
            return;
        }

        closeMobileSidebar();
    }

    function handleOutsideSidebarClick(
        event
    ) {
        if (
            isDesktop() ||
            !sidebarIsOpen()
        ) {
            return;
        }

        const sidebar =
            getSidebar();

        const button =
            getMenuButton();

        if (
            !sidebar ||
            sidebar.contains(
                event.target
            ) ||
            button?.contains(
                event.target
            )
        ) {
            return;
        }

        closeMobileSidebar();
    }

    function handleEscapeKey(
        event
    ) {
        if (
            event.key !== "Escape"
        ) {
            return;
        }

        if (
            !isDesktop() &&
            sidebarIsOpen()
        ) {
            closeMobileSidebar();
        }
    }

    /* ====================================================== */
    /* INICIALIZACIÓN                                         */
    /* ====================================================== */

    function initializeMenuButton() {
        const button =
            getMenuButton();

        const sidebar =
            getSidebar();

        if (
            !button ||
            !sidebar
        ) {
            return;
        }

        button.setAttribute(
            "aria-controls",
            sidebar.id ||
                "side-nav"
        );

        button.setAttribute(
            "aria-expanded",
            sidebarIsOpen()
                ? "true"
                : "false"
        );
    }

    function initializeLayout() {
        applySidebarState();
        initializeSubmenus();
        initializeMenuButton();
        applyHeaderOffset();

        window.setTimeout(
            applyHeaderOffset,
            50
        );

        window.setTimeout(
            applyHeaderOffset,
            250
        );

        window.setTimeout(
            applyHeaderOffset,
            600
        );
    }

    function bindEvents() {
        if (outsideClickBound) {
            return;
        }

        outsideClickBound =
            true;

        document.addEventListener(
            "click",
            handleMobileLinkClick
        );

        document.addEventListener(
            "click",
            handleOutsideSidebarClick
        );

        document.addEventListener(
            "keydown",
            handleEscapeKey
        );

        window.addEventListener(
            "resize",
            requestLayoutUpdate
        );

        window.addEventListener(
            "orientationchange",
            requestLayoutUpdate
        );

        window.addEventListener(
            "load",
            applyHeaderOffset
        );
    }

    /* ====================================================== */
    /* API GLOBAL                                             */
    /* Mantiene compatibilidad con onclick en los HTML.        */
    /* ====================================================== */

    window.toggleSidebar =
        toggleSidebar;

    window.toggleMenu =
        toggleMenu;

    window.miltelApplySidebarState =
        applySidebarState;

    window.miltelOpenSidebar =
        openSidebar;

    window.miltelCloseSidebar =
        closeSidebar;

    window.miltelCloseMobileSidebar =
        closeMobileSidebar;

    window.miltelApplyHeaderOffset =
        applyHeaderOffset;

    window.miltelInitializeSubmenus =
        initializeSubmenus;

    /* ====================================================== */
    /* ARRANQUE                                               */
    /* ====================================================== */

    bindEvents();

    if (
        document.readyState ===
        "loading"
    ) {
        document.addEventListener(
            "DOMContentLoaded",
            initializeLayout
        );
    } else {
        initializeLayout();
    }
})();