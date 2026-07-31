/* ========================================================== */
/* MILTEL TABLE COLUMNS                                        */
/* Visibilidad y persistencia de columnas en tablas            */
/* ========================================================== */

(function () {
    "use strict";

    /* ====================================================== */
    /* CONSTANTES                                             */
    /* ====================================================== */

    const STORAGE_PREFIX =
        "miltel-table-columns:";

    const DEFAULT_TABLE_SELECTOR =
        "table[data-column-table]";

    const DEFAULT_TOGGLE_SELECTOR =
        "[data-column-toggle]";

    const DEFAULT_PANEL_SELECTOR =
        "[data-column-panel]";

    const DEFAULT_TRIGGER_SELECTOR =
        "[data-column-panel-trigger]";

    const DEFAULT_RESET_SELECTOR =
        "[data-column-reset]";

    const DEFAULT_SHOW_ALL_SELECTOR =
        "[data-column-show-all]";

    const HIDDEN_CLASS =
        "hidden";

    const COLUMN_HIDDEN_CLASS =
        "miltel-column-hidden";

    /* ====================================================== */
    /* ESTADO INTERNO                                         */
    /* ====================================================== */

    const registeredTables =
        new Map();

    let globalEventsBound =
        false;

    /* ====================================================== */
    /* UTILIDADES DE STORAGE                                  */
    /* ====================================================== */

    function safeStorageGet(
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

    function safeStorageSet(
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

    function safeStorageRemove(
        key
    ) {
        try {
            localStorage.removeItem(
                key
            );

            return true;
        } catch (error) {
            return false;
        }
    }

    /* ====================================================== */
    /* UTILIDADES GENERALES                                   */
    /* ====================================================== */

    function normalizeKey(
        value
    ) {
        return String(
            value || ""
        )
            .trim()
            .toLowerCase()
            .replace(
                /\s+/g,
                "-"
            )
            .replace(
                /[^a-z0-9:_-]/g,
                "-"
            )
            .replace(
                /-+/g,
                "-"
            )
            .replace(
                /^-|-$/g,
                ""
            );
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

    function parseJson(
        raw,
        fallback = null
    ) {
        if (!raw) {
            return fallback;
        }

        try {
            const parsed =
                JSON.parse(
                    raw
                );

            return parsed;
        } catch (error) {
            return fallback;
        }
    }

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
        if (!selector) {
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

    function uniqueValues(
        values
    ) {
        return Array.from(
            new Set(
                values.filter(
                    Boolean
                )
            )
        );
    }

    /* ====================================================== */
    /* IDENTIFICACIÓN DE TABLAS                               */
    /* ====================================================== */

    function getTableKey(
        table
    ) {
        if (!table) {
            return "";
        }

        const explicitKey =
            table.dataset.columnTable ||
            table.dataset.tableKey ||
            table.id;

        if (explicitKey) {
            return normalizeKey(
                explicitKey
            );
        }

        const index =
            Array.from(
                document.querySelectorAll(
                    "table"
                )
            ).indexOf(
                table
            );

        return normalizeKey(
            `table-${index + 1}`
        );
    }

    function buildStorageKey(
        tableKey
    ) {
        return (
            STORAGE_PREFIX +
            normalizeKey(
                tableKey
            )
        );
    }

    function resolveTable(
        tableOrKey
    ) {
        if (
            tableOrKey instanceof
            HTMLTableElement
        ) {
            return tableOrKey;
        }

        if (
            typeof tableOrKey ===
            "string"
        ) {
            const directElement =
                getElement(
                    tableOrKey
                );

            if (
                directElement instanceof
                HTMLTableElement
            ) {
                return directElement;
            }

            const normalizedKey =
                normalizeKey(
                    tableOrKey
                );

            const registered =
                registeredTables.get(
                    normalizedKey
                );

            if (
                registered?.table instanceof
                HTMLTableElement
            ) {
                return registered.table;
            }

            return document.querySelector(
                `table[data-column-table="${escapeCssValue(
                    tableOrKey
                )}"]`
            );
        }

        return null;
    }

    /* ====================================================== */
    /* IDENTIFICACIÓN DE COLUMNAS                             */
    /* ====================================================== */

    function getHeaderCells(
        table
    ) {
        if (!table) {
            return [];
        }

        const headerRow =
            table.querySelector(
                "thead tr"
            );

        if (!headerRow) {
            return [];
        }

        return Array.from(
            headerRow.children
        ).filter(
            function (
                cell
            ) {
                return (
                    cell.tagName === "TH" ||
                    cell.tagName === "TD"
                );
            }
        );
    }

    function getColumnKeyFromCell(
        cell,
        index
    ) {
        if (!cell) {
            return normalizeKey(
                `column-${index + 1}`
            );
        }

        const explicitKey =
            cell.dataset.columnKey ||
            cell.dataset.column ||
            cell.getAttribute(
                "data-field"
            );

        if (explicitKey) {
            return normalizeKey(
                explicitKey
            );
        }

        const label =
            cell.textContent
                ?.trim();

        if (label) {
            return normalizeKey(
                label
            );
        }

        return normalizeKey(
            `column-${index + 1}`
        );
    }

    function ensureColumnMetadata(
        table
    ) {
        const headerCells =
            getHeaderCells(
                table
            );

        const columns = [];

        headerCells.forEach(
            function (
                headerCell,
                index
            ) {
                const key =
                    getColumnKeyFromCell(
                        headerCell,
                        index
                    );

                headerCell.dataset.columnKey =
                    key;

                headerCell.dataset.columnIndex =
                    String(index);

                const required =
                    headerCell.dataset.columnRequired ===
                        "true" ||
                    headerCell.dataset.columnLocked ===
                        "true";

                const defaultVisible =
                    headerCell.dataset.columnDefault !==
                        "hidden";

                columns.push({
                    key,
                    index,
                    label:
                        headerCell.dataset.columnLabel ||
                        headerCell.textContent?.trim() ||
                        `Columna ${index + 1}`,
                    required,
                    defaultVisible,
                    headerCell
                });
            }
        );

        return columns;
    }

    function getColumnDefinition(
        table,
        columnKey
    ) {
        const normalizedKey =
            normalizeKey(
                columnKey
            );

        const context =
            getOrCreateTableContext(
                table
            );

        return (
            context.columns.find(
                function (
                    column
                ) {
                    return (
                        column.key ===
                        normalizedKey
                    );
                }
            ) || null
        );
    }

    /* ====================================================== */
    /* CONTEXTO DE LA TABLA                                   */
    /* ====================================================== */

    function createTableContext(
        table
    ) {
        if (
            !(
                table instanceof
                HTMLTableElement
            )
        ) {
            return null;
        }

        const key =
            getTableKey(
                table
            );

        const columns =
            ensureColumnMetadata(
                table
            );

        const context = {
            key,
            table,
            columns,
            storageKey:
                table.dataset.columnStorageKey ||
                buildStorageKey(
                    key
                )
        };

        registeredTables.set(
            key,
            context
        );

        return context;
    }

    function getOrCreateTableContext(
        tableOrKey
    ) {
        const table =
            resolveTable(
                tableOrKey
            );

        if (!table) {
            return null;
        }

        const key =
            getTableKey(
                table
            );

        const existing =
            registeredTables.get(
                key
            );

        if (
            existing &&
            existing.table ===
                table
        ) {
            existing.columns =
                ensureColumnMetadata(
                    table
                );

            return existing;
        }

        return createTableContext(
            table
        );
    }

    /* ====================================================== */
    /* CELDAS DE UNA COLUMNA                                  */
    /* ====================================================== */

    function getColumnCells(
        table,
        column
    ) {
        if (
            !table ||
            !column
        ) {
            return [];
        }

        const cells = [];

        table
            .querySelectorAll(
                "tr"
            )
            .forEach(
                function (
                    row
                ) {
                    const rowCells =
                        Array.from(
                            row.children
                        ).filter(
                            function (
                                cell
                            ) {
                                return (
                                    cell.tagName ===
                                        "TH" ||
                                    cell.tagName ===
                                        "TD"
                                );
                            }
                        );

                    const cell =
                        rowCells[
                            column.index
                        ];

                    if (cell) {
                        cells.push(
                            cell
                        );
                    }
                }
            );

        return cells;
    }

    /* ====================================================== */
    /* VISIBILIDAD                                            */
    /* ====================================================== */

    function columnIsVisible(
        tableOrKey,
        columnKey
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return false;
        }

        const column =
            getColumnDefinition(
                context.table,
                columnKey
            );

        if (!column) {
            return false;
        }

        return !column.headerCell.classList.contains(
            COLUMN_HIDDEN_CLASS
        );
    }

    function setColumnVisibility(
        tableOrKey,
        columnKey,
        visible,
        options = {}
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return false;
        }

        const column =
            getColumnDefinition(
                context.table,
                columnKey
            );

        if (!column) {
            return false;
        }

        const shouldShow =
            column.required
                ? true
                : Boolean(
                    visible
                );

        const cells =
            getColumnCells(
                context.table,
                column
            );

        cells.forEach(
            function (
                cell
            ) {
                cell.classList.toggle(
                    COLUMN_HIDDEN_CLASS,
                    !shouldShow
                );

                cell.classList.toggle(
                    HIDDEN_CLASS,
                    !shouldShow
                );

                cell.setAttribute(
                    "aria-hidden",
                    shouldShow
                        ? "false"
                        : "true"
                );

                if (shouldShow) {
                    cell.style.removeProperty(
                        "display"
                    );
                } else {
                    cell.style.setProperty(
                        "display",
                        "none",
                        "important"
                    );
                }
            }
        );

        syncColumnToggles(
            context,
            column.key,
            shouldShow
        );

        if (
            options.save !==
            false
        ) {
            saveTableState(
                context
            );
        }

        context.table.dispatchEvent(
            new CustomEvent(
                "miltel:column-visibility-changed",
                {
                    bubbles: true,
                    detail: {
                        table:
                            context.table,
                        tableKey:
                            context.key,
                        columnKey:
                            column.key,
                        visible:
                            shouldShow
                    }
                }
            )
        );

        return true;
    }

    function toggleColumnVisibility(
        tableOrKey,
        columnKey,
        options = {}
    ) {
        const visible =
            columnIsVisible(
                tableOrKey,
                columnKey
            );

        return setColumnVisibility(
            tableOrKey,
            columnKey,
            !visible,
            options
        );
    }

    /* ====================================================== */
    /* ESTADO COMPLETO                                        */
    /* ====================================================== */

    function getCurrentTableState(
        tableOrKey
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return null;
        }

        const visibility = {};

        context.columns.forEach(
            function (
                column
            ) {
                visibility[
                    column.key
                ] =
                    column.required
                        ? true
                        : columnIsVisible(
                            context.table,
                            column.key
                        );
            }
        );

        return {
            version: 1,
            tableKey:
                context.key,
            updatedAt:
                Date.now(),
            visibility
        };
    }

    function saveTableState(
        tableOrKey
    ) {
        const context =
            tableOrKey?.table
                ? tableOrKey
                : getOrCreateTableContext(
                    tableOrKey
                );

        if (!context) {
            return false;
        }

        const state =
            getCurrentTableState(
                context.table
            );

        if (!state) {
            return false;
        }

        return safeStorageSet(
            context.storageKey,
            JSON.stringify(
                state
            )
        );
    }

    function getSavedTableState(
        tableOrKey
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return null;
        }

        const raw =
            safeStorageGet(
                context.storageKey
            );

        const parsed =
            parseJson(
                raw
            );

        if (
            !parsed ||
            typeof parsed.visibility !==
                "object"
        ) {
            return null;
        }

        return parsed;
    }

    function applyTableState(
        tableOrKey,
        state,
        options = {}
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (
            !context ||
            !state ||
            typeof state.visibility !==
                "object"
        ) {
            return false;
        }

        context.columns.forEach(
            function (
                column
            ) {
                const hasSavedValue =
                    Object.prototype.hasOwnProperty.call(
                        state.visibility,
                        column.key
                    );

                const visible =
                    column.required ||
                    (
                        hasSavedValue
                            ? Boolean(
                                state.visibility[
                                    column.key
                                ]
                            )
                            : column.defaultVisible
                    );

                setColumnVisibility(
                    context.table,
                    column.key,
                    visible,
                    {
                        save: false
                    }
                );
            }
        );

        if (
            options.save ===
            true
        ) {
            saveTableState(
                context
            );
        }

        return true;
    }

    function restoreTableState(
        tableOrKey
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return false;
        }

        const savedState =
            getSavedTableState(
                context.table
            );

        if (savedState) {
            return applyTableState(
                context.table,
                savedState
            );
        }

        return applyDefaultTableState(
            context.table,
            {
                save: false
            }
        );
    }

    /* ====================================================== */
    /* ESTADO PREDETERMINADO                                  */
    /* ====================================================== */

    function applyDefaultTableState(
        tableOrKey,
        options = {}
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return false;
        }

        context.columns.forEach(
            function (
                column
            ) {
                setColumnVisibility(
                    context.table,
                    column.key,
                    column.required ||
                        column.defaultVisible,
                    {
                        save: false
                    }
                );
            }
        );

        if (
            options.save !==
            false
        ) {
            saveTableState(
                context
            );
        }

        return true;
    }

    function resetTableColumns(
        tableOrKey
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return false;
        }

        safeStorageRemove(
            context.storageKey
        );

        return applyDefaultTableState(
            context.table,
            {
                save: false
            }
        );
    }

    function showAllColumns(
        tableOrKey
    ) {
        const context =
            getOrCreateTableContext(
                tableOrKey
            );

        if (!context) {
            return false;
        }

        context.columns.forEach(
            function (
                column
            ) {
                setColumnVisibility(
                    context.table,
                    column.key,
                    true,
                    {
                        save: false
                    }
                );
            }
        );

        saveTableState(
            context
        );

        return true;
    }

    /* ====================================================== */
    /* CHECKBOXES                                             */
    /* ====================================================== */

    function getToggleTableKey(
        toggle
    ) {
        return normalizeKey(
            toggle.dataset.columnTableTarget ||
            toggle.closest(
                "[data-column-controls]"
            )?.dataset.columnControls ||
            toggle.closest(
                DEFAULT_PANEL_SELECTOR
            )?.dataset.columnTableTarget ||
            ""
        );
    }

    function getToggleColumnKey(
        toggle
    ) {
        return normalizeKey(
            toggle.dataset.columnToggle ||
            toggle.value ||
            ""
        );
    }

    function findTogglesForColumn(
        context,
        columnKey
    ) {
        const normalizedColumn =
            normalizeKey(
                columnKey
            );

        return getElements(
            DEFAULT_TOGGLE_SELECTOR
        ).filter(
            function (
                toggle
            ) {
                const toggleColumn =
                    getToggleColumnKey(
                        toggle
                    );

                const toggleTable =
                    getToggleTableKey(
                        toggle
                    );

                return (
                    toggleColumn ===
                        normalizedColumn &&
                    (
                        !toggleTable ||
                        toggleTable ===
                            context.key
                    )
                );
            }
        );
    }

    function syncColumnToggles(
        context,
        columnKey,
        visible
    ) {
        findTogglesForColumn(
            context,
            columnKey
        ).forEach(
            function (
                toggle
            ) {
                if (
                    "checked" in
                    toggle
                ) {
                    toggle.checked =
                        visible;
                }

                toggle.setAttribute(
                    "aria-checked",
                    visible
                        ? "true"
                        : "false"
                );

                const column =
                    context.columns.find(
                        function (
                            item
                        ) {
                            return (
                                item.key ===
                                normalizeKey(
                                    columnKey
                                )
                            );
                        }
                    );

                if (
                    column?.required
                ) {
                    toggle.disabled =
                        true;

                    toggle.setAttribute(
                        "aria-disabled",
                        "true"
                    );
                }
            }
        );
    }

    function initializeTogglesForTable(
        context
    ) {
        context.columns.forEach(
            function (
                column
            ) {
                syncColumnToggles(
                    context,
                    column.key,
                    columnIsVisible(
                        context.table,
                        column.key
                    )
                );
            }
        );
    }

    /* ====================================================== */
    /* PANEL DE COLUMNAS                                      */
    /* ====================================================== */

    function getPanelTargetKey(
        element
    ) {
        return normalizeKey(
            element.dataset.columnTableTarget ||
            element.dataset.columnPanelTrigger ||
            element.closest(
                "[data-column-controls]"
            )?.dataset.columnControls ||
            ""
        );
    }

    function findPanelForTrigger(
        trigger
    ) {
        const explicitSelector =
            trigger.dataset.columnPanelSelector;

        if (explicitSelector) {
            return getElement(
                explicitSelector
            );
        }

        const tableKey =
            getPanelTargetKey(
                trigger
            );

        if (tableKey) {
            const matchingPanel =
                getElements(
                    DEFAULT_PANEL_SELECTOR
                ).find(
                    function (
                        panel
                    ) {
                        return (
                            normalizeKey(
                                panel.dataset
                                    .columnTableTarget
                            ) ===
                            tableKey
                        );
                    }
                );

            if (matchingPanel) {
                return matchingPanel;
            }
        }

        return trigger.nextElementSibling?.matches(
            DEFAULT_PANEL_SELECTOR
        )
            ? trigger.nextElementSibling
            : null;
    }

    function panelIsOpen(
        panel
    ) {
        return (
            panel &&
            !panel.classList.contains(
                HIDDEN_CLASS
            )
        );
    }

    function openColumnPanel(
        panelOrTrigger
    ) {
        const directPanel =
            getElement(
                panelOrTrigger
            );

        const panel =
            directPanel?.matches(
                DEFAULT_PANEL_SELECTOR
            )
                ? directPanel
                : findPanelForTrigger(
                    directPanel
                );

        if (!panel) {
            return false;
        }

        closeAllColumnPanels(
            panel
        );

        panel.classList.remove(
            HIDDEN_CLASS
        );

        panel.setAttribute(
            "aria-hidden",
            "false"
        );

        const tableKey =
            normalizeKey(
                panel.dataset
                    .columnTableTarget
            );

        const matchingTrigger =
            getElements(
                DEFAULT_TRIGGER_SELECTOR
            ).find(
                function (
                    trigger
                ) {
                    return (
                        getPanelTargetKey(
                            trigger
                        ) ===
                        tableKey
                    );
                }
            );

        if (matchingTrigger) {
            matchingTrigger.setAttribute(
                "aria-expanded",
                "true"
            );
        }

        return true;
    }

    function closeColumnPanel(
        panelOrTrigger
    ) {
        const directElement =
            getElement(
                panelOrTrigger
            );

        const panel =
            directElement?.matches(
                DEFAULT_PANEL_SELECTOR
            )
                ? directElement
                : findPanelForTrigger(
                    directElement
                );

        if (!panel) {
            return false;
        }

        panel.classList.add(
            HIDDEN_CLASS
        );

        panel.setAttribute(
            "aria-hidden",
            "true"
        );

        const tableKey =
            normalizeKey(
                panel.dataset
                    .columnTableTarget
            );

        getElements(
            DEFAULT_TRIGGER_SELECTOR
        ).forEach(
            function (
                trigger
            ) {
                if (
                    getPanelTargetKey(
                        trigger
                    ) ===
                    tableKey
                ) {
                    trigger.setAttribute(
                        "aria-expanded",
                        "false"
                    );
                }
            }
        );

        return true;
    }

    function toggleColumnPanel(
        trigger
    ) {
        const panel =
            findPanelForTrigger(
                trigger
            );

        if (!panel) {
            return false;
        }

        if (
            panelIsOpen(
                panel
            )
        ) {
            return closeColumnPanel(
                panel
            );
        }

        return openColumnPanel(
            panel
        );
    }

    function closeAllColumnPanels(
        exceptPanel = null
    ) {
        getElements(
            DEFAULT_PANEL_SELECTOR
        ).forEach(
            function (
                panel
            ) {
                if (
                    panel !==
                    exceptPanel
                ) {
                    closeColumnPanel(
                        panel
                    );
                }
            }
        );
    }

    /* ====================================================== */
    /* RESOLVER TABLA DESDE UN CONTROL                        */
    /* ====================================================== */

    function resolveTableFromControl(
        control
    ) {
        if (!control) {
            return null;
        }

        const tableKey =
            normalizeKey(
                control.dataset.columnTableTarget ||
                control.closest(
                    "[data-column-controls]"
                )?.dataset.columnControls ||
                control.closest(
                    DEFAULT_PANEL_SELECTOR
                )?.dataset.columnTableTarget ||
                ""
            );

        if (tableKey) {
            return resolveTable(
                tableKey
            );
        }

        const container =
            control.closest(
                "[data-column-container]"
            );

        if (container) {
            return container.querySelector(
                "table"
            );
        }

        return document.querySelector(
            DEFAULT_TABLE_SELECTOR
        );
    }

    /* ====================================================== */
    /* EVENTOS                                                */
    /* ====================================================== */

    function handleDocumentChange(
        event
    ) {
        const toggle =
            event.target.closest(
                DEFAULT_TOGGLE_SELECTOR
            );

        if (!toggle) {
            return;
        }

        const table =
            resolveTableFromControl(
                toggle
            );

        const columnKey =
            getToggleColumnKey(
                toggle
            );

        if (
            !table ||
            !columnKey
        ) {
            return;
        }

        const visible =
            "checked" in toggle
                ? toggle.checked
                : toggle.getAttribute(
                    "aria-checked"
                ) !== "false";

        setColumnVisibility(
            table,
            columnKey,
            visible
        );
    }

    function handleDocumentClick(
        event
    ) {
        const panelTrigger =
            event.target.closest(
                DEFAULT_TRIGGER_SELECTOR
            );

        if (panelTrigger) {
            event.preventDefault();

            toggleColumnPanel(
                panelTrigger
            );

            return;
        }

        const resetButton =
            event.target.closest(
                DEFAULT_RESET_SELECTOR
            );

        if (resetButton) {
            event.preventDefault();

            const table =
                resolveTableFromControl(
                    resetButton
                );

            if (table) {
                resetTableColumns(
                    table
                );
            }

            return;
        }

        const showAllButton =
            event.target.closest(
                DEFAULT_SHOW_ALL_SELECTOR
            );

        if (showAllButton) {
            event.preventDefault();

            const table =
                resolveTableFromControl(
                    showAllButton
                );

            if (table) {
                showAllColumns(
                    table
                );
            }

            return;
        }

        const openPanel =
            event.target.closest(
                DEFAULT_PANEL_SELECTOR
            );

        if (openPanel) {
            return;
        }

        closeAllColumnPanels();
    }

    function handleDocumentKeydown(
        event
    ) {
        if (
            event.key !==
            "Escape"
        ) {
            return;
        }

        closeAllColumnPanels();
    }

    /* ====================================================== */
    /* REGISTRO                                               */
    /* ====================================================== */

    function registerTableColumns(
        tableOrSelector
    ) {
        const table =
            resolveTable(
                tableOrSelector
            ) ||
            getElement(
                tableOrSelector
            );

        if (
            !(
                table instanceof
                HTMLTableElement
            )
        ) {
            return null;
        }

        const context =
            getOrCreateTableContext(
                table
            );

        if (!context) {
            return null;
        }

        restoreTableState(
            table
        );

        initializeTogglesForTable(
            context
        );

        return context;
    }

    function refreshTableColumns(
        tableOrKey
    ) {
        const table =
            resolveTable(
                tableOrKey
            );

        if (!table) {
            return null;
        }

        const key =
            getTableKey(
                table
            );

        registeredTables.delete(
            key
        );

        return registerTableColumns(
            table
        );
    }

    function unregisterTableColumns(
        tableOrKey,
        options = {}
    ) {
        const table =
            resolveTable(
                tableOrKey
            );

        const key =
            table
                ? getTableKey(
                    table
                )
                : normalizeKey(
                    tableOrKey
                );

        const context =
            registeredTables.get(
                key
            );

        if (
            context &&
            options.clearStorage ===
                true
        ) {
            safeStorageRemove(
                context.storageKey
            );
        }

        return registeredTables.delete(
            key
        );
    }

    /* ====================================================== */
    /* DETECCIÓN AUTOMÁTICA                                   */
    /* ====================================================== */

    function initializeDeclaredTables(
        root = document
    ) {
        getElements(
            DEFAULT_TABLE_SELECTOR,
            root
        ).forEach(
            registerTableColumns
        );
    }

    function observeDynamicTables() {
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
                                            DEFAULT_TABLE_SELECTOR
                                        )
                                    ) {
                                        registerTableColumns(
                                            node
                                        );
                                    }

                                    initializeDeclaredTables(
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
            "change",
            handleDocumentChange
        );

        document.addEventListener(
            "click",
            handleDocumentClick
        );

        document.addEventListener(
            "keydown",
            handleDocumentKeydown
        );
    }

    /* ====================================================== */
    /* API GLOBAL                                             */
    /* ====================================================== */

    window.miltelRegisterTableColumns =
        registerTableColumns;

    window.miltelRefreshTableColumns =
        refreshTableColumns;

    window.miltelUnregisterTableColumns =
        unregisterTableColumns;

    window.miltelSetColumnVisibility =
        setColumnVisibility;

    window.miltelToggleColumnVisibility =
        toggleColumnVisibility;

    window.miltelColumnIsVisible =
        columnIsVisible;

    window.miltelSaveTableColumns =
        saveTableState;

    window.miltelRestoreTableColumns =
        restoreTableState;

    window.miltelResetTableColumns =
        resetTableColumns;

    window.miltelShowAllTableColumns =
        showAllColumns;

    window.miltelOpenColumnPanel =
        openColumnPanel;

    window.miltelCloseColumnPanel =
        closeColumnPanel;

    window.miltelToggleColumnPanel =
        toggleColumnPanel;

    /* ====================================================== */
    /* ARRANQUE                                               */
    /* ====================================================== */

    function initializeTableColumns() {
        initializeDeclaredTables();
        observeDynamicTables();
    }

    bindGlobalEvents();

    if (
        document.readyState ===
        "loading"
    ) {
        document.addEventListener(
            "DOMContentLoaded",
            initializeTableColumns
        );
    } else {
        initializeTableColumns();
    }
})();