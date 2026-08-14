function getFilterRows() {

    return Array.from(
        document.querySelectorAll(
            "#enhancement-filters .enhancement-filter"
        )
    );
}


function getCurrentFilters() {

    return getFilterRows().map(
        function(row) {

            return {
                enhancement:
                    row.querySelector(
                        "select.enhancement-name"
                    ).value,

                value:
                    row.querySelector(
                        ".enhancement-value"
                    ).value,
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
        "enhancement_options",
        "1"
    );

    url.searchParams.set(
        "name",
        document.getElementById(
            "name"
        ).value
    );

    url.searchParams.set(
        "item_type",
        document.getElementById(
            "item_type"
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

	const filters =
		getCurrentFilters();

	url.searchParams.set(
		"enhancement_filter_count",
		filters.length
	);

	filters.forEach(
		function(filter, index) {

			if (filter.enhancement) {
				url.searchParams.set(
					"enhancement_" + index,
					filter.enhancement
				);
			}

			if (filter.value) {
				url.searchParams.set(
					"enhancement_value_" + index,
					filter.value
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
            "Failed to retrieve enhancement options"
        );
    }

    return response.json();
}


function populateEnhancementOptions(
    enhancementSelect,
    options,
    selectedEnhancement,
    excludedEnhancements = new Set()
) {

    enhancementSelect.innerHTML =
        '<option value="">Any enhancement</option>';

    Object.keys(options)
        .sort(
            function(a, b) {
                return a.localeCompare(b);
            }
        )
        .forEach(
            function(name) {

                /*
                 * Don't allow an enhancement that has already
                 * been selected in an earlier filter row.
                 *
                 * Keep the current row's selection available
                 * so refreshing the controls doesn't clear it.
                 */
                if (
                    name !== selectedEnhancement &&
                    excludedEnhancements.has(name)
                ) {
                    return;
                }

                const option =
                    document.createElement(
                        "option"
                    );

                option.value = name;
                option.textContent = name;

                if (
                    name === selectedEnhancement
                ) {
                    option.selected = true;
                }

                enhancementSelect.appendChild(
                    option
                );
            }
        );

    if (
        Object.prototype.hasOwnProperty.call(
            options,
            selectedEnhancement
        )
    ) {
        enhancementSelect.value =
            selectedEnhancement;
    } else {
        enhancementSelect.value = "";
    }
}


function populateEnhancementValues(
    row,
    options,
    selectedValue
) {

    const enhancementSelect =
        row.querySelector(
            "select.enhancement-name"
        );

    const valueSelect =
        row.querySelector(
            ".enhancement-value"
        );

    const enhancement =
        enhancementSelect.value;

    const values =
        options[enhancement] || [];

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
     * the selected enhancement has no values.
     */
    valueSelect.disabled =
        enhancement !== "" &&
        values.length === 0;
}


async function refreshEnhancementFilters(
    preserveValues = true
) {

    const requestId =
        ++refreshEnhancementFilters.requestId;

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
        refreshEnhancementFilters.requestId
    ) {
        return;
    }

    rows.forEach(
        function(row, index) {

            const enhancementSelect =
                row.querySelector(
                    "select.enhancement-name"
                );

            const valueSelect =
                row.querySelector(
                    ".enhancement-value"
                );

            const selectedEnhancement =
                enhancementSelect.value;

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
                    enhancements: {}
                };

            const options =
                rowData.enhancements;
				
			const excludedEnhancements =
				new Set();

			rows.forEach(
				function(otherRow, otherIndex) {

					/*
					 * Bidirectional scoping: the current
					 * row must not offer an enhancement
					 * already chosen in any other row.
					 */
					if (
						otherIndex === index
					) {
						return;
					}

					const otherEnhancement =
						otherRow.querySelector(
							"select.enhancement-name"
						).value;

					if (otherEnhancement) {
						excludedEnhancements.add(
							otherEnhancement
						);
					}
				}
			);

            /*
             * If the current enhancement is no longer
             * valid based on the current search,
             * clear it and its value.
             */
            const enhancementStillValid =
                Object.prototype.hasOwnProperty.call(
                    options,
                    selectedEnhancement
                );

            if (
                selectedEnhancement &&
                !enhancementStillValid
            ) {
                enhancementSelect.value =
                    "";

                valueSelect.value =
                    "";

                valueSelect.dataset.selectedValue =
                    "";
            }

			populateEnhancementOptions(
				enhancementSelect,
				options,
				enhancementSelect.value,
				excludedEnhancements
			);

            populateEnhancementValues(
                row,
                options,
                (
                    enhancementSelect.value ===
                    selectedEnhancement
                )
                    ? selectedValue
                    : ""
            );
        }
    );
}

refreshEnhancementFilters.requestId = 0;


async function updateEnhancementValues(
    enhancementSelect
) {

    const row =
        enhancementSelect.closest(
            ".enhancement-filter"
        );
    const valueSelect =
        row.querySelector(
            ".enhancement-value"
        );

    /*
     * Changing this enhancement invalidates
     * only this row's previous value.
     */
    valueSelect.value = "";

    valueSelect.dataset.selectedValue = "";

    await refreshEnhancementFilters(
        true
    );
}


function updateEnhancementValuesFromValue() {

    /*
     * A value change scopes every other row, so
     * refresh them all. This row's own selection
     * is preserved.
     */
    refreshEnhancementFilters(
        true
    );
}


function addEnhancement() {

    const container =
        document.getElementById(
            "enhancement-filters"
        );

    const index =
        getFilterRows().length;

    const row =
        document.createElement(
            "div"
        );

    row.className =
        "enhancement-filter";

    row.innerHTML = `
<select
    name="enhancement_${index}"
    class="enhancement-name"
>
    <option value="">Any enhancement</option>
</select>

<select
    name="enhancement_value_${index}"
    class="enhancement-value"
>
    <option value="">Any value</option>
</select>

<button
    type="button"
    class="remove-enhancement"
    onclick="removeEnhancement(this)"
>
    Remove
</button>
`;

    container.appendChild(
        row
    );

    const enhancementSelect =
        row.querySelector(
            "select.enhancement-name"
        );

    const valueSelect =
        row.querySelector(
            ".enhancement-value"
        );

    enhancementSelect.addEventListener(
        "change",
        function() {
            updateEnhancementValues(
                this
            );
        }
    );

    valueSelect.addEventListener(
        "change",
        updateEnhancementValuesFromValue
    );

    /*
     * Recalculate all rows so the new row
     * gets options based on the current search.
     */
    refreshEnhancementFilters(
        true
    );

    renumberEnhancements();
}


function removeEnhancement(
    button
) {

    button.parentElement.remove();

    renumberEnhancements();

    if (
        getFilterRows().length === 0
    ) {
        addEnhancement();

        return;
    }

    refreshEnhancementFilters(
        true
    );
}


function renumberEnhancements() {

    getFilterRows().forEach(
        function(row, index) {

            const enhancementSelect =
                row.querySelector(
                    "select.enhancement-name"
                );

            const valueSelect =
                row.querySelector(
                    ".enhancement-value"
                );

            enhancementSelect.name =
                "enhancement_" + index;

            valueSelect.name =
                "enhancement_value_" + index;
        }
    );
}


document.addEventListener(
    "DOMContentLoaded",
    function() {

        const typeSelect =
            document.getElementById(
                "item_type"
            );

        typeSelect.addEventListener(
            "change",
            function() {

                /*
                 * Type changes can invalidate
                 * any existing enhancement/value.
                 */
                refreshEnhancementFilters(
                    true
                );
            }
        );

        getFilterRows().forEach(
            function(row) {

                const enhancementSelect =
                    row.querySelector(
                        "select.enhancement-name"
                    );

                const valueSelect =
                    row.querySelector(
                        ".enhancement-value"
                    );

                enhancementSelect.addEventListener(
                    "change",
                    function() {
                        updateEnhancementValues(
                            this
                        );
                    }
                );

                valueSelect.addEventListener(
                    "change",
                    updateEnhancementValuesFromValue
                );
            }
        );

        /*
         * Populate all controls from the current
         * server-rendered selections.
         */
        refreshEnhancementFilters(
            true
        );
    }
);
