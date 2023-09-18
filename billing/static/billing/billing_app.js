/*
 * JavaScript functionality for billing app
 * Depends on jquery.js, jquery-ui.js, and billin_app.css
 */

(function($) {
    var BillingApp = {
        // Popup handler
        showPopup: function(trigger_element) {
            var popup = $('#popup'),
                trigger_data = trigger_element.data(),
                timeout = trigger_data.timeout;
            popup.fadeIn(1000);
            popup.find('span').text(trigger_data.msg);
            if (timeout !== null) setTimeout(function() { popup.fadeOut(1000); }, timeout);
        },

        // Mapping functions
        getValue: function() { return $(this).val(); },
        notValue: function(x) { return !x; },

        // Handler function that disables submit buttons based on invoice number inputs
        disableSubmitButtons: function(input) {
            var parent_form = input.parents('form'),
                input_vals = parent_form.find('input[name$=invoice_no]').map(BillingApp.getValue).get(),
                submit_buttons = parent_form.find('.gen_pdf');
            submit_buttons.prop('disabled', input_vals.every(BillingApp.notValue));
        },

        // Initializer
        initialize: function() {

            // Initializing accordion drop-downs
            $('.invoices').accordion({
                header: '.invoice_summary',
                active: false,
                event: 'dblclick',
                collapsible: true,
                heightStyle: 'content',
                icons: {
                    header: 'ui-icon-circle-triangle-e',
                    activeHeader: 'ui-icon-circle-triangle-s'
                }
            });

            // Initializing tab functionality
            $('.tabs').tabs({
                collapsible: false,
                hide: true,
                show: true,
                active: parseInt($('#step').val())
            });

            // Scripting for clearing period selection form
            $('#reset_form').click(function() {
                var parentForm = $(this).parents('form');
                parentForm.find('input[type=text]').val();
                parentForm.find('select').each(function() {
                    $(this).find('option:first').prop('selected', true);
                });
            });

            // Handling form submissions
            $('form').submit(function() {
                var add_link = $(this),
                    no_icon_path = add_link.data('no_icon'),
                    first_adj_form = add_link.siblings('table:eq(0)'),
                    last_adj_form_clone = first_adj_form.clone(),
                    new_row = $('<tr><th colspan="2"' +
                                '<span class="del_adj" title="Click here to delete this adjustment">' +
                                '<img/> Delete this adjustment</span></th></tr>'),
                    delete_link = new_row.find('span'),
                    regex = /^.+(-\d+-).+/,
                    change_attrs = ['id', 'name'];

                // Form resequencing and TOTAL_FORMS updating function
                function resequence_adj_forms() {
                    var add_link_siblings = add_link.siblings(),
                        adj_forms = add_link_siblings.filter('table'),
                        n_forms_input = add_link_siblings.filter('[name$=TOTAL_FORMS]');
                    adj_forms.each(function(i, table) {
                        $(table).find('select, input').each(function() {
                            var widget = $(this);
                            for(var attr_idx = 0; attr_idx < change_attrs.length; attr_idx++) {
                                var attr_name = change_attrs[attr_idx],
                                    attr_val = widget.attr(attr_name);
                                if (attr_val) {
                                    var matched = attr_val.match(regex),
                                        new_attr_val = matched[0].replace(matched[1], '-' + i + '-');
                                    widget.attr(attr_name, new_attr_val);
                                }
                            }
                        });
                    });

                    // Updating TOTAL_FORMS input value
                    n_forms_input.val(adj_forms.length);
                }

                // Updating src attribute of img for delete button
                new_row.find('img').attr('src', no_icon_path);

                // Removing form when delete button is clicked
                delete_link.click(function() {
                    $(this).parents('table').remove();
                    resequence_adj_forms();
                });

                // Adding delete button to cloned adjustment form, clearing, and inserting it
                new_row.appendTo(last_adj_form_clone);
                last_adj_form_clone.find('input').each(function() { $(this).val(''); });
                last_adj_form_clone.insertBefore(add_link);
                resequence_adj_forms();
            });

            // Handling invoice number clearing button
            $('input.clear_invoices').click(function() {
                var clear_button = $(this);
                clear_button.parents('form').find('input[name$=invoice_no]').val('');
                BillingApp.disableSubmitButtons(clear_button);
            });

            // Handling changes in invoice numbers
            $('input[name$=invoice_no]').keyup(function() {
                BillingApp.disableSubmitButtons($(this));
            });

            // Initializing scripting for select all checkboxes
            $('input.select_jobs').change(function() {
                var checkbox = $(this),
                    checkbox_checked = checkbox.prop('checked');
                checkbox.parents('table').find('input[name*=include]').prop('checked', checkbox_checked);
            });
        }
    };

    // Running startup scripts upon page loading
    $(BillingApp.initialize);

})(django.jQuery);
