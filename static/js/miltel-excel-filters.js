/* ========================================================== */
/* MILTEL EXCEL FILTERS                                        */
/* Paneles de filtros tipo Excel para tablas                   */
/* ========================================================== */

(function () {
    "use strict";

    /* ====================================================== */
    /* CONSTANTES                                             */
    /* ====================================================== */

    const PANEL_SELECTOR =
        "[data-excel-filter-panel], .excel-panel";

    const TRIGGER_SELECTOR =
        "[data-excel-filter-trigger], .excel-header-btn";

    const SEARCH_SELECTOR =
        "[data-excel-filter-search]";

    const OPTION_SELECTOR =
        "[data-excel-filter-option]";

    const SELECT_ALL_SELECTOR =
        "[data-excel-filter-select-all]";

    const APPLY_SELECTOR =
        "[data-excel-filter-apply]";

    const CLEAR_SELECTOR =
        "[data-excel-filter-clear]";

    const FORM_SELECTOR =
        "[data-excel-filter-form], #excel-filter-form";

    const OPTION_ROW_SELECTOR =
        "[data-excel-filter-option-row]";

    const COUNTER_SELECTOR =
        "[data-excel-filter-counter]";

    const ACTIVE_CLASS =
        "is-active";

    const OPEN_CLASS =
        "is-open";

    const HIDDEN_CLASS =
        "hidden";

    const PANEL_MARGIN =
        8;

    const VIEWPORT_MARGIN =
        10;

    /* ====================================================== */
    /* ESTADO INTERNO                                         */
    /* ====================================================== */

    let activePanel =
        null;

    let activeTrigger =
        null;

    let positionFrame =
        null;

    let globalEventsBound =
        false;

    /* ====================================================== */
    /* UTILIDADES                                             */
    /* ====================================================== */

    function getElement(
        target,
        root = document
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
            typeof target !==
            "string"
        ) {
            return null;
        }

        const byId =
            document.getElementById(
                target
            );

        if (byId) {
            return byId;
        }

        try {
            return root.querySelector(
                target
            );
        } catch (error) {
            return null;
        }
    }

    function getElements(
        selector,
        root = document
    ) {
        if (
            !selector ||
            !root
        ) {
            return [];
        }

        try {
            return Array.from(
                root.querySelectorAll(
                    selector
                )
            );
        } catch (error) {
            return [];
        }
    }

    function normalizeText(
        value
    ) {
        return String(
            value || ""
        )
            .normalize("NFD")
            .replace(
                /[\u0300-\u036f]/g,
                ""
            )
            .trim()
            .toLowerCase();
    }

    function escapeCssValue(
        value
    ) {
        if (
            window.CSS &&
            typeof window.CSS.escape ===
                "function"
        ) {
            return window.CSS.escape(
                String(value)
            );
        }

        return String(value).replace(
            /["\\]/g,
            "\\$&"
        );
    }

    function isCheckbox(
        element
    ) {
        return (
            element instanceof
                HTMLInputElement &&
            element.type ===
                "checkbox"
        );
    }

    function panelIsOpen(
        panel
    ) {
        if (!panel) {
            return false;
        }

        return (
            !panel.classList.contains(
                HIDDEN_CLASS
            ) &&
            panel.getAttribute(
                "aria-hidden"
            ) !== "true"
        );
    }

    /* ====================================================== */
    /* RESOLVER PANEL Y BOTÓN                                 */
    /* ====================================================== */

    function getPanelIdFromTrigger(
        trigger
    ) {
        if (!trigger) {
            return "";
        }

        return (
            trigger.dataset
                .excelFilterTrigger ||
            trigger.dataset
                .excelPanel ||
            trigger.getAttribute(
                "aria-controls"
            ) ||
            ""
        );
    }

    function findPanelForTrigger(
        trigger
    ) {
        if (!trigger) {
            return null;
        }

        const panelTarget =
            getPanelIdFromTrigger(
                trigger
            );

        if (panelTarget) {
            const cleanTarget =
                panelTarget.startsWith(
                    "#"
                )
                    ? panelTarget
                    : `#${escapeCssValue(
                        panelTarget
                    )}`;

            const explicitPanel =
                getElement(
                    cleanTarget
                );

            if (explicitPanel) {
                return explicitPanel;
            }
        }

        const wrapper =
            trigger.closest(
                [
                    "[data-excel-filter]",
                    ".excel-filter-wrapper",
                    "th"
                ].join(",")
            );

        if (wrapper) {
            const internalPanel =
                wrapper.querySelector(
                    PANEL_SELECTOR
                );

            if (internalPanel) {
                return internalPanel;
            }
        }

        const nextElement =
            trigger.nextElementSibling;

        if (
            nextElement &&
            nextElement.matches(
                PANEL_SELECTOR
            )
        ) {
            return nextElement;
        }

        return null;
    }

    function findTriggerForPanel(
        panel
    ) {
        if (!panel) {
            return null;
        }

        if (panel.id) {
            const escapedId =
                escapeCssValue(
                    panel.id
                );

            const explicitTrigger =
                document.querySelector(
                    [
                        `[data-excel-filter-trigger="${escapedId}"]`,
                        `[data-excel-filter-trigger="#${escapedId}"]`,
                        `[data-excel-panel="${escapedId}"]`,
                        `[aria-controls="${escapedId}"]`
                    ].join(",")
                );

            if (explicitTrigger) {
                return explicitTrigger;
            }
        }

        const wrapper =
            panel.closest(
                [
                    "[data-excel-filter]",
                    ".excel-filter-wrapper",
                    "th"
                ].join(",")
            );

        if (wrapper) {
            return wrapper.querySelector(
                TRIGGER_SELECTOR
            );
        }

        return null;
    }

    function resolvePanel(
        panelOrTrigger
    ) {
        const element =
            getElement(
                panelOrTrigger
            );

        if (!element) {
            return null;
        }

        if (
            element.matches(
                PANEL_SELECTOR
            )
        ) {
            return element;
        }

        return findPanelForTrigger(
            element
        );
    }

    function resolveTrigger(
        panelOrTrigger,
        panel = null
    ) {
        const element =
            getElement(
                panelOrTrigger
            );

        if (
            element &&
            element.matches(
                TRIGGER_SELECTOR
            )
        ) {
            return element;
        }

        return findTriggerForPanel(
            panel ||
            resolvePanel(
                panelOrTrigger
            )
        );
    }

    /* ====================================================== */
    /* FORMULARIO                                             */
    /* ====================================================== */

    function findFilterForm(
        element
    ) {
        if (!element) {
            return document.querySelector(
                FORM_SELECTOR
            );
        }

        const explicitTarget =
            element.dataset
                .excelFilterForm;

        if (explicitTarget) {
            const explicitForm =
                getElement(
                    explicitTarget
                );

            if (
                explicitForm instanceof
                HTMLFormElement
            ) {
                return explicitForm;
            }
        }

        const closestForm =
            element.closest(
                "form"
            );

        if (
            closestForm instanceof
            HTMLFormElement
        ) {
            return closestForm;
        }

        const filterContainer =
            element.closest(
                "[data-excel-filter-container]"
            );

        if (filterContainer) {
            const internalForm =
                filterContainer.querySelector(
                    FORM_SELECTOR
                );

            if (
                internalForm instanceof
                HTMLFormElement
            ) {
                return internalForm;
            }
        }

        return document.querySelector(
            FORM_SELECTOR
        );
    }

    function submitFilterForm(
        form
    ) {
        if (
            !(
                form instanceof
                HTMLFormElement
            )
        ) {
            return false;
        }

        if (
            typeof form.requestSubmit ===
                "function"
        ) {
            form.requestSubmit();
        } else {
            form.submit();
        }

        return true;
    }

    /* ====================================================== */
    /* POSICIONAMIENTO                                        */
    /* ====================================================== */

    function resetPanelPosition(
        panel
    ) {
        if (!panel) {
            return;
        }

        panel.style.removeProperty(
            "top"
        );

        panel.style.removeProperty(
            "right"
        );

        panel.style.removeProperty(
            "bottom"
        );

        panel.style.removeProperty(
            "left"
        );

        panel.style.removeProperty(
            "max-height"
        );
    }

    function positionFilterPanel(
        panel,
        trigger
    ) {
        if (
            !panel ||
            !trigger ||
            !panelIsOpen(
                panel
            )
        ) {
            return;
        }

        resetPanelPosition(
            panel
        );

        const triggerRect =
            trigger.getBoundingClientRect();

        const panelRect =
            panel.getBoundingClientRect();

        const viewportWidth =
            window.innerWidth ||
            document.documentElement
                .clientWidth;

        const viewportHeight =
            window.innerHeight ||
            document.documentElement
                .clientHeight;

        const panelWidth =
            panelRect.width ||
            panel.offsetWidth ||
            260;

        const panelHeight =
            panelRect.height ||
            panel.offsetHeight ||
            320;

        let left =
            triggerRect.left;

        let top =
            triggerRect.bottom +
            PANEL_MARGIN;

        if (
            left + panelWidth >
            viewportWidth -
                VIEWPORT_MARGIN
        ) {
            left =
                viewportWidth -
                panelWidth -
                VIEWPORT_MARGIN;
        }

        if (
            left <
            VIEWPORT_MARGIN
        ) {
            left =
                VIEWPORT_MARGIN;
        }

        const availableBelow =
            viewportHeight -
            triggerRect.bottom -
            PANEL_MARGIN -
            VIEWPORT_MARGIN;

        const availableAbove =
            triggerRect.top -
            PANEL_MARGIN -
            VIEWPORT_MARGIN;

        if (
            panelHeight >
                availableBelow &&
            availableAbove >
                availableBelow
        ) {
            top =
                triggerRect.top -
                panelHeight -
                PANEL_MARGIN;
        }

        if (
            top <
            VIEWPORT_MARGIN
        ) {
            top =
                VIEWPORT_MARGIN;
        }

        const maxAvailableHeight =
            Math.max(
                140,
                viewportHeight -
                    top -
                    VIEWPORT_MARGIN
            );

        panel.style.position =
            "fixed";

        panel.style.left =
            `${Math.round(left)}px`;

        panel.style.top =
            `${Math.round(top)}px`;

        panel.style.maxHeight =
            `${Math.round(
                maxAvailableHeight
            )}px`;

        panel.style.zIndex =
            panel.dataset.zIndex ||
            "100";
    }

    function requestPanelPosition() {
        if (
            !activePanel ||
            !activeTrigger
        ) {
            return;
        }

        if (positionFrame) {
            window.cancelAnimationFrame(
                positionFrame
            );
        }

        positionFrame =
            window.requestAnimationFrame(
                function () {
                    positionFilterPanel(
                        activePanel,
                        activeTrigger
                    );

                    positionFrame =
                        null;
                }
            );
    }

    /* ====================================================== */
    /* APERTURA Y CIERRE                                      */
    /* ====================================================== */

    function setTriggerState(
        trigger,
        open
    ) {
        if (!trigger) {
            return;
        }

        trigger.setAttribute(
            "aria-expanded",
            open
                ? "true"
                : "false"
        );

        trigger.classList.toggle(
            OPEN_CLASS,
            open
        );
    }

    function openFilterPanel(
        panelOrTrigger
    ) {
        const panel =
            resolvePanel(
                panelOrTrigger
            );

        if (!panel) {
            return false;
        }

        const trigger =
            resolveTrigger(
                panelOrTrigger,
                panel
            );

        closeAllFilterPanels(
            panel
        );

        panel.classList.remove(
            HIDDEN_CLASS
        );

        panel.classList.add(
            OPEN_CLASS
        );

        panel.setAttribute(
            "aria-hidden",
            "false"
        );

        setTriggerState(
            trigger,
            true
        );

        activePanel =
            panel;

        activeTrigger =
            trigger;

        updatePanelState(
            panel
        );

        window.requestAnimationFrame(
            function () {
                positionFilterPanel(
                    panel,
                    trigger
                );

                const searchInput =
                    panel.querySelector(
                        SEARCH_SELECTOR
                    );

                if (
                    searchInput &&
                    panel.dataset
                        .focusSearch !==
                        "false"
                ) {
                    searchInput.focus({
                        preventScroll: true
                    });
                }
            }
        );

        panel.dispatchEvent(
            new CustomEvent(
                "miltel:excel-filter-opened",
                {
                    bubbles: true,
                    detail: {
                        panel,
                        trigger
                    }
                }
            )
        );

        return true;
    }

    function closeFilterPanel(
        panelOrTrigger
    ) {
        const panel =
            resolvePanel(
                panelOrTrigger
            );

        if (!panel) {
            return false;
        }

        const trigger =
            resolveTrigger(
                panelOrTrigger,
                panel
            );

        panel.classList.add(
            HIDDEN_CLASS
        );

        panel.classList.remove(
            OPEN_CLASS
        );

        panel.setAttribute(
            "aria-hidden",
            "true"
        );

        setTriggerState(
            trigger,
            false
        );

        resetPanelPosition(
            panel
        );

        if (
            activePanel ===
            panel
        ) {
            activePanel =
                null;

            activeTrigger =
                null;
        }

        panel.dispatchEvent(
            new CustomEvent(
                "miltel:excel-filter-closed",
                {
                    bubbles: true,
                    detail: {
                        panel,
                        trigger
                    }
                }
            )
        );

        return true;
    }

    function toggleFilterPanel(
        panelOrTrigger
    ) {
        const panel =
            resolvePanel(
                panelOrTrigger
            );

        if (!panel) {
            return false;
        }

        if (
            panelIsOpen(
                panel
            )
        ) {
            return closeFilterPanel(
                panel
            );
        }

        return openFilterPanel(
            panelOrTrigger
        );
    }

    function closeAllFilterPanels(
        exceptPanel = null
    ) {
        getElements(
            PANEL_SELECTOR
        ).forEach(
            function (
                panel
            ) {
                if (
                    panel !==
                    exceptPanel &&
                    panelIsOpen(
                        panel
                    )
                ) {
                    closeFilterPanel(
                        panel
                    );
                }
            }
        );
    }

    /* ====================================================== */
    /* OPCIONES                                               */
    /* ====================================================== */

    function getPanelOptions(
        panel
    ) {
        if (!panel) {
            return [];
        }

        return getElements(
            OPTION_SELECTOR,
            panel
        ).filter(
            isCheckbox
        );
    }

    function optionIsSearchVisible(
        option
    ) {
        const row =
            option.closest(
                OPTION_ROW_SELECTOR
            ) ||
            option.closest(
                "label"
            ) ||
            option.parentElement;

        if (!row) {
            return true;
        }

        return (
            !row.classList.contains(
                HIDDEN_CLASS
            ) &&
            row.style.display !==
                "none"
        );
    }

    function getVisibleOptions(
        panel
    ) {
        return getPanelOptions(
            panel
        ).filter(
            optionIsSearchVisible
        );
    }

    function getSelectedOptions(
        panel
    ) {
        return getPanelOptions(
            panel
        ).filter(
            function (
                option
            ) {
                return option.checked;
            }
        );
    }

    function setOptionsChecked(
        options,
        checked
    ) {
        options.forEach(
            function (
                option
            ) {
                if (
                    option.disabled
                ) {
                    return;
                }

                option.checked =
                    checked;

                option.dispatchEvent(
                    new Event(
                        "change",
                        {
                            bubbles: true
                        }
                    )
                );
            }
        );
    }

    /* ====================================================== */
    /* SELECCIONAR TODO                                       */
    /* ====================================================== */

    function getSelectAllCheckbox(
        panel
    ) {
        if (!panel) {
            return null;
        }

        const checkbox =
            panel.querySelector(
                SELECT_ALL_SELECTOR
            );

        return isCheckbox(
            checkbox
        )
            ? checkbox
            : null;
    }

    function updateSelectAllState(
        panel
    ) {
        const selectAll =
            getSelectAllCheckbox(
                panel
            );

        if (!selectAll) {
            return;
        }

        const visibleOptions =
            getVisibleOptions(
                panel
            ).filter(
                function (
                    option
                ) {
                    return !option.disabled;
                }
            );

        if (
            visibleOptions.length ===
            0
        ) {
            selectAll.checked =
                false;

            selectAll.indeterminate =
                false;

            return;
        }

        const checkedCount =
            visibleOptions.filter(
                function (
                    option
                ) {
                    return option.checked;
                }
            ).length;

        selectAll.checked =
            checkedCount ===
            visibleOptions.length;

        selectAll.indeterminate =
            checkedCount > 0 &&
            checkedCount <
                visibleOptions.length;

        selectAll.setAttribute(
            "aria-checked",
            selectAll.indeterminate
                ? "mixed"
                : selectAll.checked
                    ? "true"
                    : "false"
        );
    }

    function handleSelectAllChange(
        selectAll
    ) {
        const panel =
            selectAll.closest(
                PANEL_SELECTOR
            );

        if (!panel) {
            return;
        }

        setOptionsChecked(
            getVisibleOptions(
                panel
            ),
            selectAll.checked
        );

        updatePanelState(
            panel
        );
    }

    /* ====================================================== */
    /* BÚSQUEDA                                               */
    /* ====================================================== */

    function getOptionSearchText(
        option
    ) {
        if (!option) {
            return "";
        }

        const explicitText =
            option.dataset
                .searchText;

        if (explicitText) {
            return normalizeText(
                explicitText
            );
        }

        const row =
            option.closest(
                OPTION_ROW_SELECTOR
            ) ||
            option.closest(
                "label"
            ) ||
            option.parentElement;

        return normalizeText(
            row?.textContent ||
            option.value
        );
    }

    function filterPanelOptions(
        panel,
        searchValue
    ) {
        if (!panel) {
            return 0;
        }

        const normalizedSearch =
            normalizeText(
                searchValue
            );

        let visibleCount =
            0;

        getPanelOptions(
            panel
        ).forEach(
            function (
                option
            ) {
                const row =
                    option.closest(
                        OPTION_ROW_SELECTOR
                    ) ||
                    option.closest(
                        "label"
                    ) ||
                    option.parentElement;

                if (!row) {
                    return;
                }

                const matches =
                    !normalizedSearch ||
                    getOptionSearchText(
                        option
                    ).includes(
                        normalizedSearch
                    );

                row.classList.toggle(
                    HIDDEN_CLASS,
                    !matches
                );

                row.style.display =
                    matches
                        ? ""
                        : "none";

                if (matches) {
                    visibleCount +=
                        1;
                }
            }
        );

        updateSelectAllState(
            panel
        );

        updateCounter(
            panel,
            visibleCount
        );

        return visibleCount;
    }

    /* ====================================================== */
    /* CONTADOR                                               */
    /* ====================================================== */

    function updateCounter(
        panel,
        visibleCount = null
    ) {
        if (!panel) {
            return;
        }

        const counter =
            panel.querySelector(
                COUNTER_SELECTOR
            );

        if (!counter) {
            return;
        }

        const allOptions =
            getPanelOptions(
                panel
            );

        const selectedCount =
            allOptions.filter(
                function (
                    option
                ) {
                    return option.checked;
                }
            ).length;

        const currentVisibleCount =
            visibleCount ===
            null
                ? getVisibleOptions(
                    panel
                ).length
                : visibleCount;

        const template =
            counter.dataset
                .counterTemplate;

        if (template) {
            counter.textContent =
                template
                    .replace(
                        "{selected}",
                        String(
                            selectedCount
                        )
                    )
                    .replace(
                        "{visible}",
                        String(
                            currentVisibleCount
                        )
                    )
                    .replace(
                        "{total}",
                        String(
                            allOptions.length
                        )
                    );

            return;
        }

        counter.textContent =
            `${selectedCount} de ${allOptions.length} seleccionados`;
    }

    function updatePanelState(
        panel
    ) {
        if (!panel) {
            return;
        }

        updateSelectAllState(
            panel
        );

        updateCounter(
            panel
        );
    }

    /* ====================================================== */
    /* ESTADO ACTIVO DEL BOTÓN                                */
    /* ====================================================== */

    function panelHasActiveFilter(
        panel
    ) {
        if (!panel) {
            return false;
        }

        if (
            panel.dataset.active ===
            "true"
        ) {
            return true;
        }

        const options =
            getPanelOptions(
                panel
            );

        if (
            options.length ===
            0
        ) {
            return false;
        }

        const enabledOptions =
            options.filter(
                function (
                    option
                ) {
                    return !option.disabled;
                }
            );

        const checkedOptions =
            enabledOptions.filter(
                function (
                    option
                ) {
                    return option.checked;
                }
            );

        return (
            checkedOptions.length >
                0 &&
            checkedOptions.length <
                enabledOptions.length
        );
    }

    function updateTriggerActiveState(
        panel
    ) {
        if (!panel) {
            return;
        }

        const trigger =
            findTriggerForPanel(
                panel
            );

        if (!trigger) {
            return;
        }

        const active =
            panelHasActiveFilter(
                panel
            );

        trigger.classList.toggle(
            ACTIVE_CLASS,
            active
        );

        trigger.dataset.filterActive =
            active
                ? "true"
                : "false";
    }

    /* ====================================================== */
    /* APLICAR Y LIMPIAR                                      */
    /* ====================================================== */

    function applyFilter(
        controlOrPanel
    ) {
        const element =
            getElement(
                controlOrPanel
            );

        const panel =
            element?.matches(
                PANEL_SELECTOR
            )
                ? element
                : element?.closest(
                    PANEL_SELECTOR
                );

        if (!panel) {
            return false;
        }

        updateTriggerActiveState(
            panel
        );

        const form =
            findFilterForm(
                panel
            );

        panel.dispatchEvent(
            new CustomEvent(
                "miltel:excel-filter-apply",
                {
                    bubbles: true,
                    cancelable: true,
                    detail: {
                        panel,
                        form,
                        selected:
                            getSelectedOptions(
                                panel
                            ).map(
                                function (
                                    option
                                ) {
                                    return option.value;
                                }
                            )
                    }
                }
            )
        );

        if (
            panel.dataset
                .submitOnApply ===
            "false"
        ) {
            closeFilterPanel(
                panel
            );

            return true;
        }

        return submitFilterForm(
            form
        );
    }

    function clearFilter(
        controlOrPanel
    ) {
        const element =
            getElement(
                controlOrPanel
            );

        const panel =
            element?.matches(
                PANEL_SELECTOR
            )
                ? element
                : element?.closest(
                    PANEL_SELECTOR
                );

        if (!panel) {
            return false;
        }

        const options =
            getPanelOptions(
                panel
            );

        const clearMode =
            panel.dataset
                .clearMode ||
            "all";

        const checked =
            clearMode !==
            "none";

        options.forEach(
            function (
                option
            ) {
                if (
                    option.disabled
                ) {
                    return;
                }

                option.checked =
                    checked;
            }
        );

        const searchInput =
            panel.querySelector(
                SEARCH_SELECTOR
            );

        if (searchInput) {
            searchInput.value =
                "";

            filterPanelOptions(
                panel,
                ""
            );
        }

        panel.dataset.active =
            "false";

        updatePanelState(
            panel
        );

        updateTriggerActiveState(
            panel
        );

        panel.dispatchEvent(
            new CustomEvent(
                "miltel:excel-filter-cleared",
                {
                    bubbles: true,
                    detail: {
                        panel
                    }
                }
            )
        );

        const form =
            findFilterForm(
                panel
            );

        if (
            panel.dataset
                .submitOnClear ===
            "false"
        ) {
            closeFilterPanel(
                panel
            );

            return true;
        }

        return submitFilterForm(
            form
        );
    }

    /* ====================================================== */
    /* INICIALIZAR PANEL                                      */
    /* ====================================================== */

    function initializeFilterPanel(
        panel
    ) {
        if (
            !panel ||
            panel.dataset
                .miltelExcelInitialized ===
                "1"
        ) {
            return;
        }

        panel.dataset
            .miltelExcelInitialized =
            "1";

        const trigger =
            findTriggerForPanel(
                panel
            );

        if (
            trigger &&
            panel.id
        ) {
            trigger.setAttribute(
                "aria-controls",
                panel.id
            );
        }

        panel.setAttribute(
            "aria-hidden",
            panelIsOpen(
                panel
            )
                ? "false"
                : "true"
        );

        setTriggerState(
            trigger,
            panelIsOpen(
                panel
            )
        );

        const searchInput =
            panel.querySelector(
                SEARCH_SELECTOR
            );

        if (searchInput) {
            filterPanelOptions(
                panel,
                searchInput.value
            );
        } else {
            updatePanelState(
                panel
            );
        }

        updateTriggerActiveState(
            panel
        );
    }

    function initializeFilterTrigger(
        trigger
    ) {
        if (
            !trigger ||
            trigger.dataset
                .miltelExcelInitialized ===
                "1"
        ) {
            return;
        }

        trigger.dataset
            .miltelExcelInitialized =
            "1";

        const panel =
            findPanelForTrigger(
                trigger
            );

        trigger.setAttribute(
            "aria-haspopup",
            "true"
        );

        trigger.setAttribute(
            "aria-expanded",
            panelIsOpen(
                panel
            )
                ? "true"
                : "false"
        );

        if (
            panel &&
            !panel.id
        ) {
            panel.id =
                `miltel-excel-panel-${Math.random()
                    .toString(36)
                    .slice(2, 10)}`;
        }

        if (panel?.id) {
            trigger.setAttribute(
                "aria-controls",
                panel.id
            );
        }
    }

    function initializeExcelFilters(
        root = document
    ) {
        getElements(
            PANEL_SELECTOR,
            root
        ).forEach(
            initializeFilterPanel
        );

        getElements(
            TRIGGER_SELECTOR,
            root
        ).forEach(
            initializeFilterTrigger
        );
    }

    /* ====================================================== */
    /* EVENTOS                                                */
    /* ====================================================== */

    function handleDocumentClick(
        event
    ) {
        const trigger =
            event.target.closest(
                TRIGGER_SELECTOR
            );

        if (trigger) {
            event.preventDefault();
            event.stopPropagation();

            toggleFilterPanel(
                trigger
            );

            return;
        }

        const applyButton =
            event.target.closest(
                APPLY_SELECTOR
            );

        if (applyButton) {
            event.preventDefault();

            applyFilter(
                applyButton
            );

            return;
        }

        const clearButton =
            event.target.closest(
                CLEAR_SELECTOR
            );

        if (clearButton) {
            event.preventDefault();

            clearFilter(
                clearButton
            );

            return;
        }

        if (
            activePanel &&
            (
                activePanel.contains(
                    event.target
                ) ||
                activeTrigger?.contains(
                    event.target
                )
            )
        ) {
            return;
        }

        closeAllFilterPanels();
    }

    function handleDocumentInput(
        event
    ) {
        const searchInput =
            event.target.closest(
                SEARCH_SELECTOR
            );

        if (!searchInput) {
            return;
        }

        const panel =
            searchInput.closest(
                PANEL_SELECTOR
            );

        filterPanelOptions(
            panel,
            searchInput.value
        );
    }

    function handleDocumentChange(
        event
    ) {
        const selectAll =
            event.target.closest(
                SELECT_ALL_SELECTOR
            );

        if (
            selectAll &&
            isCheckbox(
                selectAll
            )
        ) {
            handleSelectAllChange(
                selectAll
            );

            return;
        }

        const option =
            event.target.closest(
                OPTION_SELECTOR
            );

        if (
            !option ||
            !isCheckbox(
                option
            )
        ) {
            return;
        }

        const panel =
            option.closest(
                PANEL_SELECTOR
            );

        updatePanelState(
            panel
        );
    }

    function handleDocumentKeydown(
        event
    ) {
        if (
            event.key ===
            "Escape"
        ) {
            closeAllFilterPanels();
            return;
        }

        if (
            event.key !==
            "Enter"
        ) {
            return;
        }

        const searchInput =
            event.target.closest(
                SEARCH_SELECTOR
            );

        if (!searchInput) {
            return;
        }

        const panel =
            searchInput.closest(
                PANEL_SELECTOR
            );

        if (
            panel?.dataset
                .applyOnEnter ===
            "false"
        ) {
            return;
        }

        event.preventDefault();

        applyFilter(
            panel
        );
    }

    function handleViewportChange() {
        requestPanelPosition();
    }

    /* ====================================================== */
    /* CONTENIDO DINÁMICO                                     */
    /* ====================================================== */

    function observeDynamicFilters() {
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
                            mutation.addedNodes.forEach(
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

                                    if (
                                        node.matches(
                                            PANEL_SELECTOR
                                        )
                                    ) {
                                        initializeFilterPanel(
                                            node
                                        );
                                    }

                                    if (
                                        node.matches(
                                            TRIGGER_SELECTOR
                                        )
                                    ) {
                                        initializeFilterTrigger(
                                            node
                                        );
                                    }

                                    initializeExcelFilters(
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
    /* API PARA ACTUALIZACIONES AJAX                          */
    /* ====================================================== */

    function refreshExcelFilters(
        root = document
    ) {
        const resolvedRoot =
            getElement(
                root
            ) ||
            root ||
            document;

        initializeExcelFilters(
            resolvedRoot
        );

        return true;
    }

    function markFilterActive(
        panelOrTrigger,
        active = true
    ) {
        const panel =
            resolvePanel(
                panelOrTrigger
            );

        if (!panel) {
            return false;
        }

        panel.dataset.active =
            active
                ? "true"
                : "false";

        updateTriggerActiveState(
            panel
        );

        return true;
    }

    /* ====================================================== */
    /* COMPATIBILIDAD CON FUNCIONES INLINE                    */
    /* ====================================================== */

    function toggleExcelFilter(
        element
    ) {
        return toggleFilterPanel(
            element
        );
    }

    function openExcelFilter(
        element
    ) {
        return openFilterPanel(
            element
        );
    }

    function closeExcelFilter(
        element
    ) {
        return closeFilterPanel(
            element
        );
    }

    function applyExcelFilter(
        element
    ) {
        return applyFilter(
            element
        );
    }

    function clearExcelFilter(
        element
    ) {
        return clearFilter(
            element
        );
    }

    function filterExcelOptions(
        input
    ) {
        const element =
            getElement(
                input
            );

        if (!element) {
            return 0;
        }

        const panel =
            element.closest(
                PANEL_SELECTOR
            );

        return filterPanelOptions(
            panel,
            element.value
        );
    }

    function toggleAllExcelOptions(
        checkbox
    ) {
        const element =
            getElement(
                checkbox
            );

        if (
            !element ||
            !isCheckbox(
                element
            )
        ) {
            return;
        }

        handleSelectAllChange(
            element
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
            "input",
            handleDocumentInput
        );

        document.addEventListener(
            "change",
            handleDocumentChange
        );

        document.addEventListener(
            "keydown",
            handleDocumentKeydown
        );

        window.addEventListener(
            "resize",
            handleViewportChange
        );

        window.addEventListener(
            "orientationchange",
            handleViewportChange
        );

        document.addEventListener(
            "scroll",
            handleViewportChange,
            true
        );
    }

    /* ====================================================== */
    /* API GLOBAL                                             */
    /* ====================================================== */

    window.miltelInitializeExcelFilters =
        initializeExcelFilters;

    window.miltelRefreshExcelFilters =
        refreshExcelFilters;

    window.miltelOpenExcelFilter =
        openFilterPanel;

    window.miltelCloseExcelFilter =
        closeFilterPanel;

    window.miltelToggleExcelFilter =
        toggleFilterPanel;

    window.miltelCloseAllExcelFilters =
        closeAllFilterPanels;

    window.miltelApplyExcelFilter =
        applyFilter;

    window.miltelClearExcelFilter =
        clearFilter;

    window.miltelFilterExcelOptions =
        filterPanelOptions;

    window.miltelMarkExcelFilterActive =
        markFilterActive;

    /*
     * Nombres simples para conservar temporalmente los
     * onclick existentes en diferentes plantillas.
     */
    window.toggleExcelFilter =
        toggleExcelFilter;

    window.openExcelFilter =
        openExcelFilter;

    window.closeExcelFilter =
        closeExcelFilter;

    window.applyExcelFilter =
        applyExcelFilter;

    window.clearExcelFilter =
        clearExcelFilter;

    window.filterExcelOptions =
        filterExcelOptions;

    window.toggleAllExcelOptions =
        toggleAllExcelOptions;

    /* ====================================================== */
    /* ARRANQUE                                               */
    /* ====================================================== */

    function startExcelFilters() {
        initializeExcelFilters();
        observeDynamicFilters();
    }

    bindGlobalEvents();

    if (
        document.readyState ===
        "loading"
    ) {
        document.addEventListener(
            "DOMContentLoaded",
            startExcelFilters
        );
    } else {
        startExcelFilters();
    }
})();