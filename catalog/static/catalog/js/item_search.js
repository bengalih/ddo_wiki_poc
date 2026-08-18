function getFilterRows() {

    return Array.from(
        document.querySelectorAll(
            "#enchantment-filters .enchantment-filter"
        )
    );
}


function getCurrentFilters() {

    return getFilterRows().map(
        function(row) {

            const valueSelect =
                row.querySelector(
                    ".enchantment-value"
                );

            const minInput =
                row.querySelector(
                    ".enchantment-min"
                );

            return {
                enchantment:
                    row.querySelector(
                        "select.enchantment-name"
                    ).value,

                value:
                    valueSelect.value,

                min:
                    minInput ? minInput.value : "",
            };
        }
    );
}


function buildOptionsUrl() {

    const url =
        new URL(
            window.location.href
        );

    url.search = "";

    url.searchParams.set(
        "enchantment_options",
        "1"
    );

    url.searchParams.set(
        "name",
        document.getElementById(
            "name"
        ).value
    );

    url.searchParams.set(
        "category",
        document.getElementById(
            "category"
        ).value
    );

    url.searchParams.set(
        "type",
        document.getElementById(
            "type"
        ).value
    );

    url.searchParams.set(
        "min_level",
        document.getElementById(
            "min_level"
        ).value
    );

    url.searchParams.set(
        "max_level",
        document.getElementById(
            "max_level"
        ).value
    );

    url.searchParams.set(
        "include_upgrades",
        document.getElementById(
            "include_upgrades"
        ).checked
            ? "1"
            : "0"
    );

	const filters =
		getCurrentFilters();

	url.searchParams.set(
		"enchantment_filter_count",
		filters.length
	);

	filters.forEach(
		function(filter, index) {

			if (filter.enchantment) {
				url.searchParams.set(
					"enchantment_" + index,
					filter.enchantment
				);
			}

			if (filter.value) {
				url.searchParams.set(
					"enchantment_value_" + index,
					filter.value
				);
			}

			if (filter.min) {
				url.searchParams.set(
					"enchantment_min_" + index,
					filter.min
				);
			}
		}
	);

    return url;
}


async function getAvailableOptions() {

    const response =
        await fetch(
            buildOptionsUrl(),
            {
                headers: {
                    "X-Requested-With":
                        "XMLHttpRequest"
                }
            }
        );

    if (!response.ok) {
        throw new Error(
            "Failed to retrieve enchantment options"
        );
    }

    return response.json();
}


function populateEnchantmentOptions(
    enchantmentSelect,
    options,
    labels,
    selectedEnchantment,
    excludedEnchantments = new Set()
) {

    enchantmentSelect.innerHTML =
        '<option value="">Any enchantment</option>';

    Object.keys(options)
        .sort(
            function(a, b) {
                // Sort on what the user sees (the label), so a
                // display-name override like "Bloop" sorts under
                // the B's, not where the wiki name "Seeker" sits.
                const labelA = labels[a] || a;
                const labelB = labels[b] || b;

                return labelA.localeCompare(labelB);
            }
        )
        .forEach(
            function(name) {

                /*
                 * Don't allow an enchantment that has already
                 * been selected in an earlier filter row.
                 *
                 * Keep the current row's selection available
                 * so refreshing the controls doesn't clear it.
                 */
                if (
                    name !== selectedEnchantment &&
                    excludedEnchantments.has(name)
                ) {
                    return;
                }

                const option =
                    document.createElement(
                        "option"
                    );

                option.value = name;
                option.textContent =
                    labels[name] || name;

                if (
                    name === selectedEnchantment
                ) {
                    option.selected = true;
                }

                enchantmentSelect.appendChild(
                    option
                );
            }
        );

    if (
        Object.prototype.hasOwnProperty.call(
            options,
            selectedEnchantment
        )
    ) {
        enchantmentSelect.value =
            selectedEnchantment;
    } else {
        enchantmentSelect.value = "";
    }
}


function populateEnchantmentValues(
    row,
    rowData,
    selectedValue
) {

    const enchantmentSelect =
        row.querySelector(
            "select.enchantment-name"
        );

    const valueSelect =
        row.querySelector(
            ".enchantment-value"
        );

    const minInput =
        row.querySelector(
            ".enchantment-min"
        );

    const enchantment =
        enchantmentSelect.value;

    const options =
        rowData.enchantments || {};

    const values =
        options[enchantment] || [];

    valueSelect.innerHTML =
        '<option value="">Any value</option>';

    values.forEach(
        function(value) {

            const option =
                document.createElement(
                    "option"
                );

            option.value = value;
            option.textContent = value;

            if (
                value === selectedValue
            ) {
                option.selected = true;
            }

            valueSelect.appendChild(
                option
            );
        }
    );

		if (
			values.includes(selectedValue)
		) {
			valueSelect.value =
				selectedValue;
		} else {
			valueSelect.value = "";
		}

		valueSelect.dataset.selectedValue =
			valueSelect.value;

    /*
     * A value selector is disabled only when
     * the selected enchantment has no values.
     */
    valueSelect.disabled =
        enchantment !== "" &&
        values.length === 0;

    if (minInput) {
        const hasMagnitudes =
            (rowData.has_magnitudes || {})[enchantment]
            === true;

        minInput.disabled =
            enchantment === "" ||
            !hasMagnitudes;

        /*
         * A minimum overrides an exact pick: while the
         * minimum is set the value dropdown stays visible
         * (as a flavor reference) but cannot be changed.
         */
        if (
            minInput.value !== "" &&
            !minInput.disabled
        ) {
            valueSelect.disabled = true;
        }
    }
}


async function refreshEnchantmentFilters(
    preserveValues = true
) {

    const requestId =
        ++refreshEnchantmentFilters.requestId;

    const rows =
        getFilterRows();

    let data;

    try {
        data =
            await getAvailableOptions();
    } catch (error) {
        console.error(
            error
        );

        return;
    }

    /*
     * Ignore stale responses. Rapid changes can fire
     * overlapping requests; only the latest one may
     * update the controls.
     */
    if (
        requestId !==
        refreshEnchantmentFilters.requestId
    ) {
        return;
    }

    rows.forEach(
        function(row, index) {

            const enchantmentSelect =
                row.querySelector(
                    "select.enchantment-name"
                );

            const valueSelect =
                row.querySelector(
                    ".enchantment-value"
                );

            const selectedEnchantment =
                enchantmentSelect.value;

			const selectedValue =
				preserveValues
					? (
						valueSelect.value ||
						valueSelect.dataset.selectedValue ||
						""
					)
					: "";

            const rowData =
                data.rows[index] || {
                    enchantments: {}
                };

            const options =
                rowData.enchantments;

            const labels =
                rowData.labels || {};

			const excludedEnchantments =
				new Set();

			rows.forEach(
				function(otherRow, otherIndex) {

					/*
					 * Bidirectional scoping: the current
					 * row must not offer an enchantment
					 * already chosen in any other row.
					 */
					if (
						otherIndex === index
					) {
						return;
					}

					const otherEnchantment =
						otherRow.querySelector(
							"select.enchantment-name"
						).value;

					if (otherEnchantment) {
						excludedEnchantments.add(
							otherEnchantment
						);
					}
				}
			);

            /*
             * If the current enchantment is no longer
             * valid based on the current search,
             * clear it and its value.
             */
            const enchantmentStillValid =
                Object.prototype.hasOwnProperty.call(
                    options,
                    selectedEnchantment
                );

            if (
                selectedEnchantment &&
                !enchantmentStillValid
            ) {
                enchantmentSelect.value =
                    "";

                valueSelect.value =
                    "";

                valueSelect.dataset.selectedValue =
                    "";
            }

			populateEnchantmentOptions(
				enchantmentSelect,
				options,
				labels,
				enchantmentSelect.value,
				excludedEnchantments
			);

            populateEnchantmentValues(
                row,
                rowData,
                (
                    enchantmentSelect.value ===
                    selectedEnchantment
                )
                    ? selectedValue
                    : ""
            );
        }
    );

    const typeSelect =
        document.getElementById(
            "type"
        );

    const typeOptions =
        data.types || [];

    const selectedType =
        preserveValues
            ? typeSelect.value
            : "";

    typeSelect.innerHTML =
        '<option value="">All types</option>';

    typeOptions.forEach(
        function(type) {

            const option =
                document.createElement(
                    "option"
                );

            option.value =
                type.value;
            option.textContent =
                type.label;

            if (
                type.value ===
                selectedType
            ) {
                option.selected = true;
            }

            typeSelect.appendChild(
                option
            );
        }
    );

    if (
        typeOptions.some(
            function(type) {
                return type.value ===
                    selectedType;
            }
        )
    ) {
        typeSelect.value =
            selectedType;
    } else {
        typeSelect.value = "";
    }
}

refreshEnchantmentFilters.requestId = 0;


async function updateEnchantmentValues(
    enchantmentSelect
) {

    const row =
        enchantmentSelect.closest(
            ".enchantment-filter"
        );
    const valueSelect =
        row.querySelector(
            ".enchantment-value"
        );

    /*
     * Changing this enchantment invalidates
     * only this row's previous value.
     */
    valueSelect.value = "";

    valueSelect.dataset.selectedValue = "";

    await refreshEnchantmentFilters(
        true
    );
}


function updateEnchantmentValuesFromValue() {

    /*
     * A value change scopes every other row, so
     * refresh them all. This row's own selection
     * is preserved.
     */
    refreshEnchantmentFilters(
        true
    );
}


function addEnchantment() {

    const container =
        document.getElementById(
            "enchantment-filters"
        );

    const index =
        getFilterRows().length;

    const row =
        document.createElement(
            "div"
        );

    row.className =
        "enchantment-filter";

    row.innerHTML = `
<select
    name="enchantment_${index}"
    class="enchantment-name"
>
    <option value="">Any enchantment</option>
</select>

<select
    name="enchantment_value_${index}"
    class="enchantment-value"
>
    <option value="">Any value</option>
</select>

<input
    type="number"
    name="enchantment_min_${index}"
    class="enchantment-min"
    min="0"
    step="any"
    placeholder="Min"
    title="Match items with this enchantment at or above this magnitude (e.g. 20 matches +22% and +26%)"
>

<button
    type="button"
    class="remove-enchantment"
    onclick="removeEnchantment(this)"
>
    Remove
</button>
`;

    container.appendChild(
        row
    );

    const enchantmentSelect =
        row.querySelector(
            "select.enchantment-name"
        );

    const valueSelect =
        row.querySelector(
            ".enchantment-value"
        );

    enchantmentSelect.addEventListener(
        "change",
        function() {
            updateEnchantmentValues(
                this
            );
        }
    );

    valueSelect.addEventListener(
        "change",
        updateEnchantmentValuesFromValue
    );

    const minInput =
        row.querySelector(
            ".enchantment-min"
        );

    minInput.addEventListener(
        "input",
        function() {
            refreshEnchantmentFilters(
                true
            );
        }
    );

    /*
     * Recalculate all rows so the new row
     * gets options based on the current search.
     */
    refreshEnchantmentFilters(
        true
    );

    renumberEnchantments();
}


function removeEnchantment(
    button
) {

    button.parentElement.remove();

    renumberEnchantments();

    if (
        getFilterRows().length === 0
    ) {
        addEnchantment();

        return;
    }

    refreshEnchantmentFilters(
        true
    );
}


function renumberEnchantments() {

    getFilterRows().forEach(
        function(row, index) {

            const enchantmentSelect =
                row.querySelector(
                    "select.enchantment-name"
                );

            const valueSelect =
                row.querySelector(
                    ".enchantment-value"
                );

            const minInput =
                row.querySelector(
                    ".enchantment-min"
                );

            enchantmentSelect.name =
                "enchantment_" + index;

            valueSelect.name =
                "enchantment_value_" + index;

            if (minInput) {
                minInput.name =
                    "enchantment_min_" + index;
            }
        }
    );
}


document.addEventListener(
    "DOMContentLoaded",
    function() {

        const includeUpgradesCheckbox =
            document.getElementById(
                "include_upgrades"
            );

        if (
            includeUpgradesCheckbox
        ) {
            includeUpgradesCheckbox.addEventListener(
                "change",
                function() {
                    refreshEnchantmentFilters(
                        true
                    );
                }
            );
        }

        const typeSelect =
            document.getElementById(
                "type"
            );

        typeSelect.addEventListener(
            "change",
            function() {
                refreshEnchantmentFilters(
                    true
                );
            }
        );

        const categorySelect =
            document.getElementById(
                "category"
            );

        categorySelect.addEventListener(
            "change",
            function() {
                refreshEnchantmentFilters(
                    true
                );
            }
        );

        getFilterRows().forEach(
            function(row) {

                const enchantmentSelect =
                    row.querySelector(
                        "select.enchantment-name"
                    );

                const valueSelect =
                    row.querySelector(
                        ".enchantment-value"
                    );

                const minInput =
                    row.querySelector(
                        ".enchantment-min"
                    );

                enchantmentSelect.addEventListener(
                    "change",
                    function() {
                        updateEnchantmentValues(
                            this
                        );
                    }
                );

                valueSelect.addEventListener(
                    "change",
                    updateEnchantmentValuesFromValue
                );

                if (minInput) {
                    minInput.addEventListener(
                        "input",
                        function() {
                            refreshEnchantmentFilters(
                                true
                            );
                        }
                    );
                }
            }
        );

        /*
         * Populate all controls from the current
         * server-rendered selections.
         */
        refreshEnchantmentFilters(
            true
        );
    }
);
