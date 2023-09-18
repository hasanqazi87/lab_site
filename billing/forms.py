# This file defines classes that render forms that are used to process user input

from cProfile import label
from collections import OrderedDict
from django import forms
from django.forms import formset_factory
from macola.models import InvoiceCategory
from sales_tracking.forms import MonthSelectorField


date_input = forms.DateInput(attrs={'type': 'date'})
text_area = forms.Textarea(attrs={'rows': 1, 'cols': 55})
small_text_input = forms.TextInput(attrs={'class': 'thin'})
kind_choices = (
    ('Credit', 'Credit'),
    ('Debit', 'Debit'),
)
acct_type_choices = (
    ('Regular Account', 'Regular Account'),
    ('Correctional Center Account', 'Correctional Center Account'),
    ('Inmate Benefit Fund Account', 'Inmate Benefit Fund Account'),
    ('Employee Benefit Fund Account', 'Employee Benefit Fund Account'),
    ('Department of Transportation Account', 'Department of Transportation Account'),
    ('DHS Mental Health and DD Center Account', 'DHS Mental Health and DD Center Account'),
    ('Other State Agency Account', 'Other State Agency Account'),
    ('University Account', 'University Account'),
    ('City, County, Village Account', 'City, County, Village Account'),
    ('Not For Profit Account', 'Not For Profit Account'),
)


class InvoicePeriodForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super(InvoicePeriodForm, self).__init__(*args, **kwargs)
        self.fields.update(period=MonthSelectorField(label='Select Month', help_text='Goes by FISCAL YEAR'))
        cat_qset = InvoiceCategory.objects.order_by('number')
        for pk, cat, inv in cat_qset.values_list('id', 'description', 'invoice_start'):
            fname = 'start_{}'.format(pk)
            field = forms.CharField(min_length=8, max_length=8, initial=inv,
                                    label='Starting {} Invoice Number'.format(cat),
                                    help_text='Must be exactly 8 digits long using format: '
                                              '(<4-DIGIT PREFIX><4-DIGIT SEQUENCE>)')
            self.fields.update({fname: field})
        query_by_choices = zip(('ship_date', 'enter_date'), ('Ship Date', 'Enter Date'))
        query_by_choicefield = forms.ChoiceField(choices=query_by_choices, label='Query By', initial='ship_date',
                                                 widget=forms.RadioSelect, help_text='Date field to query by')
        self.fields.update(query_by=query_by_choicefield)


class InvoiceNumberingForm(forms.Form):
    invoice_no = forms.CharField(min_length=8, max_length=50, label='Invoice Number(s)', required=False)


class IncludeJobForm(forms.Form):
    include = forms.BooleanField(required=False)


class AdjustmentForm(forms.Form):
    kind = forms.ChoiceField(choices=kind_choices, initial='Credit', label='Adjustment Type')
    ref = forms.CharField(max_length=10, label='Reference No', required=False,
                          help_text='Use Job ID if pertaining to a job')
    des = forms.CharField(max_length=30, label='Description', help_text='Brief reason for adjustment',
                          required=False)
    amount = forms.DecimalField(decimal_places=2, help_text='Include tax if adjusting for taxable jobs')


class CreditAdjustmentForm(forms.Form):
    inv_no = forms.CharField(max_length=10, label='Invoice Number', widget=small_text_input)
    sales = forms.DecimalField(decimal_places=2, label='Sales Amount', widget=small_text_input)
    tax = forms.DecimalField(decimal_places=2, label='Sales Tax', widget=small_text_input)
    reason = forms.CharField(max_length=60, label='Brief Reason for Credit', widget=text_area)


class MacolaRequestForm(forms.Form):
    req_date = forms.DateField(widget=date_input, label='Request Date')
    req_person = forms.CharField(max_length=30, label='Person Requesting MACOLA Number')
    acct_type = forms.ChoiceField(choices=acct_type_choices, widget=forms.RadioSelect,
                                  label='Type of Account Requested')

    def __init__(self, acct_no=None, *args, **kwargs):
        super(MacolaRequestForm, self).__init__(*args, **kwargs)
        if acct_no is None:
            field_names = ('tax_exemption', 'name', 'inv_addr1', 'inv_addr2', 'inv_city', 'inv_state', 'inv_zip',
                           'phone', 'fax_No', 'email')
            form_fields = (
                forms.CharField(max_length=15, label='Tax Exemption Number', required=False),
                forms.CharField(max_length=30, label='Account Name'),
                forms.CharField(max_length=30, label='Address Line 1'),
                forms.CharField(max_length=30, label='Address Line 2', required=False),
                forms.CharField(max_length=20, label='City'),
                forms.CharField(max_length=2, label='State', initial='IL'),
                forms.CharField(max_length=10, label='Zip Code'),
                forms.CharField(max_length=15, label='Phone Number'),
                forms.CharField(max_length=15, label='Fax Number', required=False),
                forms.EmailField(max_length=30, label='Email Address', required=False),
            )
            self.fields.update(OrderedDict(zip(field_names, form_fields)))
        extra_field_names = ('state_employee', 'agency_loc', 'agency_no', 'fund_no')
        extra_form_fields = (
            forms.BooleanField(label='State Employee?', required=False),
            forms.CharField(max_length=20, label='State Agency Location', initial='Springfield',
                            help_text="For Employee's Individual Account"),
            forms.IntegerField(label='Agency Number', initial=426, help_text='426 for ICI'),
            forms.IntegerField(label='Fund Number', initial=301, help_text='301 for ICI'),
        )
        self.fields.update(OrderedDict(zip(extra_field_names, extra_form_fields)))


class InvoicePathForm(forms.Form):
    def __init__(self, add_fields=None, *args, **kwargs):
        super(InvoicePathForm, self).__init__(*args, **kwargs)
        if add_fields is not None:
            self.fields.update(add_fields)
        self.fields.update(invoice_date=forms.DateField(widget=date_input, label='Invoice Date'))


class CreditRequestForm(forms.Form):
    req_date = forms.DateField(widget=date_input, label='Request Date')
    req_person = forms.CharField(max_length=30, label='Person Requesting Credit')
    acct = forms.CharField(max_length=10, label='Account Number')
    credit_no = forms.IntegerField(label='Credit Request Number')


InvoiceFormset = formset_factory(InvoiceNumberingForm, extra=0)
IncludeJobFormset = formset_factory(IncludeJobForm, extra=0)
AdjustmentFormset = formset_factory(AdjustmentForm, extra=1)
