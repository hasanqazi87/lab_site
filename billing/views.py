# This file defines server-side view functions which are passed in requests from clients, generate responses,
# and return them to the client

# Reportlab imports
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph as P,
    Table as T,
    SimpleDocTemplate,
    Image,
    PageBreak,
    TableStyle,
)

# XlsxWriter import
from xlsxwriter import Workbook

# Other built-in Python imports
import os
import pandas as pd
from datetime import timedelta, date
from copy import deepcopy
from decimal import Decimal as Dec, getcontext as get_decimal_context, ROUND_HALF_UP
from operator import attrgetter, itemgetter
from math import ceil
from re import compile as re_compile
from collections import OrderedDict

# Django imports
from django.views.generic import FormView
from django.http import HttpResponse
from django.forms import FilePathField, Form, formset_factory
from . import forms as bf
from macola.models import MacolaAcct, InvoiceCategory, BillToProvider
from lab_site_admin.pdf_templates import InvoiceTemplate, InvoiceRegisterTemplate, InvoiceImage, RegisterParagraph
from lab_site_admin.utils import currency, VISTAR_CONNECTION, get_labsite_db_connection

today = date.today()
get_decimal_context().rounding = ROUND_HALF_UP
currency_or_blank = lambda v: currency(v, default='')


class BillingAppViewMixin:
    """
    View mixin class that is subclassed to account for different environments
    """
    proxy_static_dir = '/var/www/wsgi-scripts/static/billing'

    def on_localhost(self):
        """
        Determine whether or not app is run locally.
        :return: boolean
        """
        return 'localhost' in self.request.get_host().lower()

    def get_ici_logo_path(self):
        """
        Retrieve path for ICI logo image
        :return: str
        """
        filename = 'ici_logo.png'
        if self.on_localhost():
            return os.path.join(os.environ['PWD'], 'billing/static/billing/', filename)
        return os.path.join(self.proxy_static_dir, filename)


class BillingInvoiceFormView(FormView, BillingAppViewMixin):
    """
    Main view class for the Invoicing App which handles forms that query all jobs for a given period and
    generates invoices
    """
    template_name = 'billing/billing_form.html'
    form_class = bf.InvoicePeriodForm
    proxy_invoice_dir = '/home/proxyserver/clerks/exports/'
    AIS_acct_no = '1000426986279402'
    SAP_acct_no = '9001087365'

    @staticmethod
    def get_vistar_billing_df(clean_data):
        """
        Build a Pandas Dataframe with billing info
        :param clean_data: dict
        :return: DataFrame
        """
        sql = """
            select
                vjd.bill_customer_no as acct,
                vjd.job_id,
                nvl(to_char(vjd.enter_date, 'mm/dd/yyyy'), 'N/A') as enter_date,
                vjd.frame_name,
                vjq.frame_item_no,
                vjq.name as frame_name2,
                vjd.comment1,
                nvl(to_char(vjd.ship_date, 'mm/dd/yyyy'), 'N/A') as ship_date,
                vjd.patient_last_name || ', ' || vjd.patient_first_name as patient_name,
                (select nvl(sum(amt), 0) from prism.vlm_job_item
                 where job_id = vjd.job_id and item_type = 'L') as lens_price,
                (select nvl(sum(amt), 0) from prism.vlm_job_item
                 where job_id = vjd.job_id and item_type in ('F', 'V')) as frame_price,
                vjd.job_net as sales,
            from prism.vlm_job_detail vjd
            inner join prism.lm_job lj on vjd.job_id = lj.job_id
            inner join prism.v_jobqry vjq on vjd.job_id = vjq.job_id
            where to_char({query_by}, 'yyyy-mm') = '{end_period}'
                and vjd.bill_customer_no not in ('1', '2', '3')
                and vjd.job_net > 0
              """.format(**clean_data)
        return pd.read_sql_query(sql, VISTAR_CONNECTION)
    
    def get_macola_df(self):
        """
        Build a dataframe of MACOLA accounts
        :return: DataFrame
        """
        sql = """
            select
                ma.account_No as acct,
                ma.bt_provider_id as provider,
                ma.tax_rate
            from macola_accounts ma
              """
        connection = get_labsite_db_connection(on_localhost=self.on_localhost())
        return pd.read_sql_query(sql, connection)
    
    def get_cache_path(self):
        """
        Retrieve path for HDF data file
        :return: str
        """
        filename = 'billing_data.h5'
        if self.on_localhost():
            return os.path.join(os.environ['PWD'], 'billing/static/billing/', filename)
        return os.path.join(self.proxy_static_dir, filename)
    
    def get_cached_data(self):
        """
        Retrieve cached data from HDF file
        :return: DataFrame
        """
        with pd.HDFStore(self.get_cache_path()) as hdf_store:
            return hdf_store['billing_data']
    
    def get_savepath_kwargs(self):
        """
        Build kwargs for instantiating InvoicePathForm
        :return: dict
        """
        save_path = (os.path.join('/home/', '{}/'.format(os.environ['USER'])) if self.on_localhost()
                     else self.proxy_invoice_dir)
        save_to_field = FilePathField(save_path, allow_folders=True, allow_files=False, label_suffix='',
                                      label='Save to {}'.format(save_path), match=r'^[^\.].+')
        return {'add_fields': OrderedDict(save_to=save_to_field)}
    
    @staticmethod
    def get_ici_account():
        """
        Retrieve ICI account if it exists
        :return: Model instance or None
        """
        ici = MacolaAcct.objects.filter(account_No='ici')
        return ici.first() if ici.exists() else None
    
    @staticmethod
    def get_object(queryset, pk):
        """
        Get object from passed-in primary key
        :param queryset: QuerySet
        :param pk: str
        :return: Model instance or None
        """
        qs = queryset.filter(pk=pk)
        return qs.first() if qs.exists() else None
    
    @staticmethod
    def set_totals(obj, data_df):
        """
        Set sales, tax, and totals for passed-in objects
        :param obj: Model instance
        :param data_df: DataFrame
        :return: None
        """
        if obj is not None:
            sales_sum, tax_sum = data_df.sales.sum(), data_df.tax.sum()
            obj.sales = sales_sum
            obj.tax = tax_sum
            obj.total = sales_sum + tax_sum
    
    @staticmethod
    def convert_to_currency(adjustment):
        """
        Change last value in list to currency string
        :param adjustment: list
        :return: list
        """
        *other, amount = adjustment
        return [*other, currency_or_blank(amount)]
    
    def get_billing_data(self, clean_data, from_cache=False):
        """
        Merge, process, and group data from both Vision Star and Lab Site DBs for rendering in form template
        :param clean_data: dict
        :param from_cache: boolean
        :return: DataFrame
        """
        if from_cache:
            merged = self.get_cached_data()
        else:
            merged = pd.merge(self.get_vistar_billing_df(clean_data), self.get_macola_df(), on='acct')
            merged.tax_rate.fillna(0, inplace=True)
            merged.provider.fillna(0, inplace=True)
            merged['tax'] = merged.sales * merged.tax_rate
            merged['total'] = (1 + merged.tax_rate) * merged.sales
            merged.sort_values(by=['cat', 'provider', 'acct', 'ship_date', 'patient_name'], inplace=True)
            merged.to_hdf(self.get_cache_path(), key='billing_data')
        get_object, set_totals = attrgetter('get_object', 'set_totals')(self)
        all_inv_cats, all_providers = InvoiceCategory.objects.all(), BillToProvider.objects.all()
        all_accts, billing_data, form_fieldname = MacolaAcct.objects.all(), [], 'start_{}'
        savepath_kwargs = dict(self.get_savepath_kwargs(), initial={'invoice_date': clean_data['end']})
        for inv_cat_id, inv_cat_df in merged.groupby('cat'):
            inv_cat_obj = get_object(all_inv_cats, inv_cat_id)
            set_totals(inv_cat_obj, inv_cat_df)
            invoice_start = clean_data[form_fieldname.format(inv_cat_obj.number)]
            prefix, sequence, fmt = invoice_start[:-4], int(invoice_start[-4:]), '{}{:0>4}'
            sequence_range = range(sequence, sequence + len(inv_cat_df.acct.drop_duplicates()))
            invoice_no_range = tuple(map(lambda v: fmt.format(prefix, v), sequence_range))
            invoice_initial = [{'invoice_no': inv_no} for inv_no in invoice_no_range]
            invoice_no_prefix = '{}_inv'.format(inv_cat_id)
            invoice_no_formset = bf.InvoiceFormset(initial=invoice_initial, prefix=invoice_no_prefix)
            invoice_no_forms = invoice_no_formset.forms
            inv_cat_obj.invoice_no_manager = invoice_no_formset.management_form
            inv_cat_obj.savepath_form = bf.InvoicePathForm(**savepath_kwargs)
            inv_cat_obj.has_providers = (inv_cat_df.provider != 0).any()
            invoice_no_idx, inv_cat_obj.macolas_needed = 0, 0
            for provider_id, provider_df in inv_cat_df.groupby('provider'):
                provider_obj = get_object(all_providers, provider_id)
                set_totals(provider_obj, provider_df)
                for acct_no, acct_df in provider_df.groupby('acct'):
                    include_initial = [{'include': True}] * len(acct_df)
                    include_prefix = '{}_inc'.format(acct_no)
                    include_formset = bf.IncludeJobFormset(initial=include_initial, prefix=include_prefix)
                    adj_prefix = '{}_adj'.format(acct_no)
                    adj_formset = bf.AdjustmentFormset(prefix=adj_prefix)
                    acct_obj = get_object(all_accts, acct_no)
                    set_totals(acct_obj, acct_df)
                    acct_obj.invoice_no_form = invoice_no_forms[invoice_no_idx]
                    acct_obj.adj_formset = adj_formset
                    acct_obj.include_manager = include_formset.management_form
                    if provider_obj is None and not acct_obj.macola_No:
                        inv_cat_obj.macolas_needed += 1
                    invoice_no_idx += 1
                    for include_form, job_dict in zip(include_formset, acct_df.to_dict(orient='records')):
                        job_dict.update(acct=acct_obj, provider=provider_obj, cat=inv_cat_obj,
                                        include_form=include_form)
                        billing_data.append(job_dict)
        return billing_data
    
    @staticmethod
    def validate_form(bound_form, field_name=None):
        """
        Validate bound forms and formsets and process, return clean data
        :param bound_form: Form instance
        :param field_name: str
        :return: Form instance, dict, DataFrame, Series, or None
        """
        if bound_form.is_valid():
            clean_data = bound_form.cleaned_data
            if isinstance(clean_data, dict):
                return clean_data
            filtered = [data for data in clean_data if data]
            if not filtered:
                return None
            clean_data = pd.DataFrame(filtered)
            if isinstance(bound_form, bf.AdjustmentFormset):
                clean_data['amount'] = clean_data['amount'] * clean_data.kind.map({'Credit': -1, 'Debit': 1})
                clean_data = clean_data.reindex_axis(['kind', 'ref', 'des', 'amount'], axis=1)
            if field_name is None or field_name not in clean_data:
                return clean_data
            return clean_data[field_name]
        return bound_form
    
    def form_valid(self, form):
        """
        Process main InvoicePeriodForm
        :param form: Form instance
        :return: TemplateResponse
        """
        cd = form.cleaned_data
        d = cd['period'][1]
        day_of_week = d.weekday()
        end = d - timedelta(days=day_of_week - 4) if day_of_week in (5, 6) else d
        cd.update(end=end, end_period=end.strftime('%Y-%m'))
        if not self.get_ici_account():
            error_msg = "Account 'ici' does not exist. Please create one with that account number and the lab's info"
            form.add_error(None, error_msg)
            return self.form_invalid(form)
        billing_data = self.get_billing_data(cd)
        context = self.get_context_data(billing_data=billing_data, step=1, form=form, cd=cd)
        return self.render_to_response(context)
    
    def post(self, request, *args, **kwargs):
        """
        Process all post requests
        :param request: HttpRequest
        :param args: url args
        :param kwargs: url kwargs
        :return: invocation of appropriate method
        """
        post_data, validate_form = request.POST, self.validate_form
        misc_submits = [k[1:] for k in post_data if k.startswith('_')]
        if not misc_submits:
            return super(BillingInvoiceFormView, self).post(request, *args, **kwargs)
        # Setting some general attributes and validating some forms
        cat_id = int(post_data['cat_id'])
        self.category_obj = self.get_object(InvoiceCategory.objects.filter(pk=cat_id), cat_id)
        inv_formset = bf.InvoiceFormset(data=post_data, prefix='{}_inv'.format(cat_id))
        invoice_no_list = validate_form(inv_formset, field_name='invoice_no').tolist()
        savepath_form = bf.InvoicePathForm(data=post_data, **self.get_savepath_kwargs())
        savepath_clean_data = validate_form(savepath_form)
        if isinstance(savepath_clean_data, Form):
            return self.form_invalid(savepath_clean_data)
        self.savepath_clean_data = savepath_clean_data
        self.all_providers, self.all_accounts = BillToProvider.objects.all(), MacolaAcct.objects.all()
        # Creating general styling objects
        styles = getSampleStyleSheet()
        normal, h1, h2, h3, h4 = itemgetter('Normal', 'Heading1', 'Heading2', 'Heading3', 'Heading4')(styles)
        h4.alignment, normal.fontSize, normal.leading = 2, 8, 8
        h1.alignment = h2.alignment = h3.alignment = normal.alignment = 1
        h1.borderPadding = 0
        [setattr(self, k, v) for k, v in zip(['normal', 'h1', 'h2', 'h3', 'h4'], [normal, h1, h2, h3, h4])]
        # Filtering cached billing data
        billing_data, include_job_mask, adjustments, invoice_nos = self.get_cached_data(), [], {}, {}
        billing_data = billing_data[billing_data.cat == cat_id]
        for acct_no, invoice_no in zip(billing_data.acct.drop_duplicates(), invoice_no_list):
            include_formset = bf.IncludeJobFormset(data=post_data, prefix='{}_inc'.format(acct_no))
            include_job_mask.extend(validate_form(include_formset, field_name='include').tolist())
            adj_formset = bf.AdjustmentFormset(data=post_data, prefix='{}_adj'.format(acct_no))
            adj_formset_clean_data = validate_form(adj_formset)
            adjustments[acct_no] = adj_formset_clean_data if isinstance(adj_formset_clean_data, pd.DataFrame) else None
            invoice_nos[acct_no] = invoice_no
        return getattr(self, misc_submits.pop())(billing_data[include_job_mask], adjustments, invoice_nos)
    
    def register(self, billing_data, adjustments, invoice_nos):
        """
        Generate table data and modify styling for invoice registers that have no providers
        (i.e. Non-Institutional Accounts), before passing data off to the build_register method
        :param billing_data: DataFrame
        :param adjustments: dict
        :param invoice_nos: dict
        :return: HttpResponse
        """
        table_style = TableStyle([
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONT', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('INNERGRID', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOX', (0, 0), (-1, -1), 2, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.black),
        ])
        inv_no_regex = re_compile(r',')
        get_object, set_totals = attrgetter('get_object', 'set_totals')(self)
        all_providers, all_accounts = attrgetter('all_providers', 'all_accounts')(self)
        table_data = [['Invoice No', 'Account Description', 'Sales', 'Sales Tax', 'Adjustments', 'Total']]
        row, total_adjs, included_accts = 1, [], [k for k, v in invoice_nos.items() if v]
        for provider_id, provider_df in billing_data.groupby('provider'):
            provider_obj = get_object(all_providers, provider_id)
            set_totals(provider_obj, provider_df)
            prov_adjs = []
            for acct_no, acct_df in provider_df.groupby('acct'):
                adj_df = adjustments[acct_no]
                if acct_no not in included_accts:
                    continue
                acct_adjs = adj_df.amount.sum() if isinstance(adj_df, pd.DataFrame) else 0
                prov_adjs.append(acct_adjs)
                acct_obj = get_object(all_accounts, acct_no)
                set_totals(acct_obj, acct_df)
                acct_des = '{} - #{}'.format(*attrgetter('name', 'account_No')(acct_obj))
                email = acct_obj.email
                acct_des = '{}\n{}'.format(acct_des, email) if email else acct_des
                table_data.append([
                    inv_no_regex.sub('\n', invoice_nos[acct_no]),
                    acct_des,
                    currency_or_blank(acct_obj.sales),
                    currency_or_blank(acct_obj.tax),
                    currency_or_blank(acct_adjs),
                    currency_or_blank(Dec(acct_obj.total) + acct_adjs),
                ])
                row += 1
            prov_adj_sum = sum(prov_adjs)
            total_adjs.append(prov_adj_sum)
            if provider_obj is not None:
                prov_name = provider_obj.short_name or provider_df.name[:20]
                included_prov_acct_df = provider_df[provider_df.acct.isin(included_accts)]
                table_data.append([
                    '',
                    '{} Subtotals'.format(prov_name),
                    currency(included_prov_acct_df.sales.sum()),
                    currency_or_blank(included_prov_acct_df.tax.sum()),
                    currency_or_blank(prov_adj_sum),
                    currency(Dec(included_prov_acct_df.total.sum()) + prov_adj_sum),
                ])
                table_style.add('LINEBELOW', (0, row), (-1, row), 2, colors.black)
                table_style.add('FONT', (0, row), (-1, row), 'Helvetica-Bold')
                row += 1
        included_total_acct_df = billing_data[billing_data.acct.isin(included_accts)]
        total_adj_sum = sum(total_adjs)
        sales_sum, tax_sum = included_total_acct_df.sales.sum(), included_total_acct_df.tax.sum()
        table_data.append([
            '',
            'Totals',
            currency(sales_sum),
            currency_or_blank(tax_sum),
            currency_or_blank(total_adj_sum),
            currency(Dec(sales_sum + tax_sum) + total_adj_sum),
        ])
        total_pages = ceil((len(table_data) - 29) / 31) + 1
        # Creating response object
        month_yr = self.savepath_clean_data['invoice_date'].strftime('%B %Y')
        cat_obj, h1 = attrgetter('category_obj', 'h1')(self)
        cat_des = cat_obj.description
        response = HttpResponse(content_type='application/pdf')
        filename = '{} {} Invoice Register.pdf'.format(month_yr, cat_des)
        response['Content-Disposition'] = 'attachment; filename={}'.format(filename)
        header_text = '{} Invoice Register<br/>{}'.format(cat_des, month_yr)
        story = [
            RegisterParagraph(header_text, style=h1, total_pages=total_pages),
            T(table_data, style=table_style, repeatRows=True),
        ]
        # Building PDF Document Object
        half_inch = 0.5 * inch
        inv_register_doc = InvoiceRegisterTemplate(response, topMargin=half_inch, bottomMargin=half_inch,
                                                   leftMargin=half_inch, rightMargin=half_inch,
                                                   allowSplitting=True, pagesize=letter, title=filename)
        inv_register_doc.build(story)
        return response
    
    def invoices(self, billing_data, adjustments, invoice_nos):
        """
        Generate table data, modify styling, and generate individual PDFs for each account as well as a single PDF
        containing all accounts to make printing easier
        :param billing_data: DataFrame
        :param adjustments: dict
        :param invoice_nos: dict
        :return: HttpResponse
        """
        # Getting general attributes and functions
        all_providers = all_accounts = attrgetter('all_providers', 'all_accounts')(self)
        get_object, set_totals = attrgetter('get_object', 'set_totals')(self)
        sap_acct_no, category_obj = attrgetter('SAP_acct_no', 'category_obj')(self)
        cat_des = category_obj.description
        ici, savepath_clean_data = self.get_ici_account(), self.savepath_clean_data
        inv_date_obj, save_to = itemgetter('invoice_date', 'save_to')(savepath_clean_data)
        inv_period, inv_date = inv_date_obj.strftime('%B %Y'), inv_date_obj.strftime('%m/%d/%Y')
        convert_to_currency = self.convert_to_currency
        compute_total_pages = lambda *args: ceil((sum(map(len, args)) - 29) / 48) + 1
        # Initializing styling objects
        normal, h1, h3 = attrgetter('normal', 'h1', 'h3')(self)
        info_tablestyle = TableStyle([
            ('ALIGN', (0, 0), (0, 3), 'RIGHT'),
            ('FONT', (0, 0), (0, 3), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (1, 3), 9),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ])
        addr_tablestyle = TableStyle([
            ('FONT', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('LINEBELOW', (-1, -2), (-1, -2), 1, colors.black),
            ('ALIGN', (-1, -2), (-1, -1), 'CENTER'),
            ('LEFTPADDING', (-1, 0), (-1, -3), 0.75 * inch),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        jobs_tablestyle = TableStyle([
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
        ])
        totals_tablestyle = TableStyle([
            ('FONT', (-2, 0), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (-2, 0), (-2, -1), 'RIGHT'),
            ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (-3, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        # Building general info objects
        image_path, half_inch = self.get_ici_logo_path(), 0.5 * inch
        image_dims = {'width': 1.2 * inch, 'height': half_inch}
        ici_info = [
            *attrgetter('name', 'inv_addr1', 'inv_addr2')(ici),
            '{}, {} {}'.format(*attrgetter('inv_city', 'inv_state', 'inv_zip')(ici)),
            'Phone: {} | Fax: {}'.format(*attrgetter('phone', 'fax_No')(ici)),
            '{} for SAP Users'.format(sap_acct_no),
        ]
        payment_info_paragraph = P('<br/>'.join(ici_info), normal)
        info_table_kwargs = {
            'colWidths': (1.1 * inch, 0.7 * inch),
            'rowHeights': 0.2 * inch,
            'style': info_tablestyle,
        }
        addr_table_kwargs = {
            'colWidths': 3.75 * inch,
            'rowHeights': 0.2 * inch,
            'style': addr_tablestyle,
        }
        jobs_table_kwargs = {
            'colWidths': (inch, inch, inch, 3.5 * inch, inch),
            'rowHeights': 0.2 * inch,
            'style': jobs_tablestyle,
            'repeatRows': True,
        }
        totals_table_kwargs = {
            'rowHeights': 0.25 * inch,
            'colWidths': (inch, inch, 2 * inch, inch, 1.5 * inch, inch),
        }
        invoice_doc_kwargs = {
            'leftMargin': half_inch,
            'rightMargin': half_inch,
            'topMargin': half_inch,
            'bottomMargin': half_inch,
            'allowSplitting': True,
            'pagesize': letter,
            'title': '{} {} Invoices'.format(inv_period, cat_des),
        }
        stories, inv_no_regex = [], re_compile(r'\d+')
        # Generating and saving each invoice file
        for provider_id, provider_df in billing_data.groupby('provider'):
            provider_obj = get_object(all_providers, provider_id)
            attr_or_blank = lambda attr: getattr(provider_obj, attr, '')
            for acct_no, acct_df in provider_df.groupby('acct'):
                invoice_no = inv_no_regex.findall(invoice_nos[acct_no])
                if not invoice_no:
                    continue
                invoice_no = invoice_no[0]
                acct_obj = get_object(all_accounts, acct_no)
                set_totals(acct_obj, acct_df)
                macola = acct_obj.macola_No if provider_obj is None else provider_obj.macola_No
                info_table_data = [
                    ['Invoice Period:', inv_period],
                    ['Invoice Date:', inv_date],
                    ['Invoice No:', invoice_no],
                    ['Customer Account No:', macola],
                ]
                # Adding invoice and bill-to addresses
                inv_addr_args = ('inv_city', 'inv_state', 'inv_zip')
                addr_table_data = [
                    ['{} #{}'.format(acct_obj.name, acct_obj.account_No), attr_or_blank('name')],
                    [acct_obj.inv_addr1, attr_or_blank('inv_addr1')],
                    [acct_obj.inv_addr2, attr_or_blank('inv_addr2')],
                    ['{}, {} {}'.format(*attrgetter(*inv_addr_args)(acct_obj)),
                     '{}, {} {}'.format(*attrgetter(*inv_addr_args)(provider_obj)) if provider_obj is not None else ''],
                    ['', attr_or_blank('email')],
                    ['', ''],
                    [acct_obj.email, ici.contact_name],
                    ['', ici.contact_title],
                ]
                # Building job table
                jobs_table_data = [['Job ID No', 'Enter Date', 'Ship Date', 'Patient Last, First Name', 'Price']]
                jobs_df = acct_df[['job_id', 'enter_date', 'ship_date', 'patient_name', 'sales']]
                jobs_table_data.extend(list(map(convert_to_currency, jobs_df.values.tolist())))
                # Subtotal
                total_data = [['', '', '', '', 'Subtotal:', currency(acct_obj.sales)]]
                # Adding tax
                totals_tablestyle_copy = deepcopy(totals_tablestyle)
                tax, row = acct_obj.tax, 1
                if tax:
                    row += 1
                    total_data.append(['', '', '', '',
                                       r'{:.2f}% Sales Tax:'.format(acct_obj.tax_rate * 100),
                                       currency(tax),
                    ])
                # Adding adjustments
                adj_df = adjustments[acct_no]
                if isinstance(adj_df, pd.DataFrame):
                    adj_sum = adj_df.amount.sum()
                    totals_tablestyle_copy.add('FONT', (0, row), (-1, row), 'Helvetica-Bold')
                    totals_tablestyle_copy.add('LINEBELOW', (0, row), (-3, row), 1, colors.black)
                    total_data.append(['Adjustment', 'Reference No', 'Description', 'Amount'])
                    total_data.extend(list(map(convert_to_currency, adj_df.values.tolist())))
                    total_data.extend([
                        [''] * 6,
                        ['', '', '', '', 'Adjustment Total:', currency_or_blank(adj_sum)],
                    ])
                else:
                    adj_sum = 0
                # Adding invoice grand total
                total_data.append(['', '', '', '', 'Invoice Total:', currency(Dec(acct_obj.total) + adj_sum)])
                # Building story object
                total_pages = compute_total_pages(jobs_table_data, total_data)
                story = [
                    InvoiceImage(image_path, total_pages=total_pages, acct=acct_no, **image_dims),
                    P('Illinois Correctional Industries<br/>Invoice', h1),
                    P('<u>SEND PAYMENT TO:</u>', h3),
                    payment_info_paragraph,
                    T(info_table_data, **info_table_kwargs),
                    T(addr_table_data, **addr_table_kwargs),
                    T(jobs_table_data, **jobs_table_kwargs),
                    T(total_data, **totals_table_kwargs, style=totals_tablestyle_copy),
                    PageBreak(),
                ]
                stories.extend(deepcopy(story))
                invoice_path = os.path.join(save_to, '{}.pdf'.format(invoice_no))
                invoice_doc = InvoiceTemplate(invoice_path, **invoice_doc_kwargs)
                invoice_doc.build(story)
        # Creating and building invoices doc
        filename = '{} {} Invoices.pdf'.format(inv_period, cat_des)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename={}'.format(filename)
        invoices_doc = InvoiceTemplate(response, **invoice_doc_kwargs)
        invoices_doc.build(stories)
        # Saving new starting invoice number
        invoice_seq, last_inv_regex = [], re_compile(r'\d+')
        [invoice_seq.extend(last_inv_regex.findall(inv)) for inv in invoice_nos.values()]
        last_invoice_no = max(invoice_seq)
        category_obj.invoice_start = '{}{:0>4}'.format(last_invoice_no[:-4], int(last_invoice_no[-4:]) + 1)
        category_obj.save()
        return response
    
    def summary(self, billing_data, adjustments, invoice_nos):
        """
        Generate billing summaries for all invoicing categories that have providers by listing queried jobs
        and corresponding data in a tabular format
        :param billing_data: DataFrame
        :param adjustments: dict
        :param invoice_nos: dict
        :return: HttpResponse
        """
        invoice_date = self.savepath_clean_data['invoice_date'].strftime('%B %Y')
        username = self.request.user.username
        all_providers, all_accounts = attrgetter('all_providers', 'all_accounts')(self)
        font_size = 10
        header = {
            'bold': True,
            'font_size': font_size,
            'text_h_align': 2,
            'text_v_align': 2,
            'text_wrap': True,
            'top': 2,
            'bottom': 2,
            'right': 2,
            'left': 2,
        }
        normal = {'font_size': font_size, 'text_h_align': 2, 'text_v_align': 2}
        total = {
            'font_size': font_size,
            'bold': True,
            'text_h_align': 2,
            'num_format': '#,##0',
            'text_v_align': 2,
        }
        money = {
            'font_size': font_size,
            'text_h_align': 2,
            'num_format': '$#,##0.00_);($#,##0.00)',
            'text_v_align': 2,
        }
        total_money = dict(money, bold=True)
        col_headers = (
            'Invoice No',
            'Account No',
            'Shipped To',
            'Job ID No',
            'Patient Name',
            'Frame Style',
            'Ship Date',
            'Lens Price',
            'Frame Price',
            'Total Price',
        )
        header_text = '&C&18&"Arial,Bold"{} ({})\nEyeglass Billing Summary - {}'
        filename = '{} Billing Summary'.format(invoice_date)
        response = HttpResponse(content_type='application/xlsx')
        response['Content-Disposition'] = 'attachment; filename={}.xlsx'.format(filename)
        wb = Workbook(response)
        for provider_id, provider_df in billing_data.groupby('provider'):
            p = all_providers.get(pk=provider_id)
            short_name = p.short_name or p.name[:10]
            header_format = wb.add_format(header)
            normal_format = wb.add_format(normal)
            total_format = wb.add_format(total)
            money_format = wb.add_format(money)
            total_money_format = wb.add_format(total_money)
            wb.set_properties({'title': filename, 'subject': filename, 'author': username})
            ws = wb.add_worksheet(short_name)
            ws.set_landscape()
            ws.center_horizontally()
            ws.fit_to_pages(1, 0)
            ws.set_margins(top=1.25, bottom=0.75, left=0.5, right=0.5)
            ws.set_header(header_text.format(p.name, p.macola_No, invoice_date))
            ws.set_footer('&CPage &P of &N')
            ws.repeat_rows(0)
            ws.set_default_row(13)
            ws.set_row(0, 26)
            cols_widths = (
                ('C:C', 17),
                ('E:E', 25),
                ('F:F', 20),
                ('G:G', 12),
                ('H:J', 10),
            )
            [ws.set_column(cols, width) for cols, width in cols_widths]
            ws.write_row(0, 0, col_headers, cell_format=header_format)
            row, row_range = 1, [2]
            for acct_no, acct_df in provider_df.groupby('acct'):
                invoice_no, acct_adjs = invoice_nos['acct_no'], adjustments['acct_no']
                if not invoice_no:
                    continue
                acct = all_accounts.get(account_No=acct_no)
                name = acct.short_name or acct.name
                for j in acct_df.to_dict(orient='records'):
                    patient_name = j['patient_name']
                    if ('stock' or 'frame') in patient_name.lower():
                        disp_frame = j['comment1']
                    elif not j['frame_item_no']:
                        disp_frame = j['frame_name2']
                    else:
                        disp_frame = j['frame_name']
                    row_data = (
                        invoice_no,
                        acct_no,
                        name,
                        j['job_id'],
                        patient_name,
                        disp_frame,
                        j['ship_date'],
                    )
                    ws.write_row(row, 0, row_data, normal_format)
                    ws.write_row(row, 7, itemgetter('lens_price', 'frame_price', 'sales')(j),
                                 cell_format=money_format)
                    row += 1
                if acct_adjs is not None:
                    for adj in acct_adjs.to_dict(orient='records'):
                        adj_data = (
                            invoice_no,
                            acct_no,
                            name,
                            *itemgetter('ref', 'des', 'kind')(adj),
                        )
                        ws.write_row(row, 0, adj_data, cell_format=normal_format)
                        ws.write_number(row, 9, adj['amount'], cell_format=money_format)
                        row += 1
            row_range.append(row)
            ws.write_string(row, 4, 'TOTAL PATIENTS:', cell_format=total_format)
            ws.write_number(row, 5, len(provider_df), cell_format=total_format)
            formulas = (
                '=SUM(H{}:H{})'.format(*row_range),
                '=SUM(I{}:I{})'.format(*row_range),
                '=SUM(J{}:J{})'.format(*row_range),
            )
            ws.write_row(row, 7, formulas, cell_format=total_money_format)
        return response
    
    def credit(self, billing_data, adjustments, invoice_nos):
        """
        Generate a credit request for submitted account number
        :param billing_data: DataFrame
        :param adjustments: dict
        :param invoice_nos: dict
        :return: HttpResponse
        """
        # Getting general info
        acct_no = self.request.POST['_credit'].split()[-1]
        acct_adjs_df, acct_inv_no = adjustments['acct_no'], invoice_nos['acct_no']
        ici = self.get_ici_account()
        contact_name = ici.contact_name
        # Creating styling objects
        normal, h2, h4 = attrgetter('normal', 'h2', 'h4')(self)
        h2.alignment = 0
        info_table_style = TableStyle([
            ('FONT', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 14),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        adj_table_style = TableStyle([
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 14),
            ('ALIGNMENT', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        ])
        # Creating data objects
        half_inch = 0.5 * inch
        ici_info = [
            *attrgetter('name', 'inv_addr1', 'inv_addr2')(ici),
            '{}, {} {}'.format(*attrgetter('inv_city', 'inv_state', 'inv_zip')(ici)),
            'Phone: {} | Fax: {}'.format(*attrgetter('phone', 'fax_No')(ici)),
            'Email: {}'.format(ici.email),
            'Hours of Operation: M-F 6:00 am - 2:00 pm',
        ]
        info_table_data = [
            ['Date:', today.strftime('%B %d, %Y')],
            ['To:', 'Central Fiscal Office'],
            ['From:', contact_name],
            ['', 'Dixon Optical Lab (0562)'],
            ['Subject', 'Credit Memo Request'],
        ]
        adj_table_info = [['Adjustment Type', 'Reference No', 'Description', 'Amount']]
        adj_table_info.extend(list(map(self.convert_to_currency, acct_adjs_df.values)))
        # Creating story object
        story = [
            Image(self.get_ici_logo_path(), width=1.2 * inch, height=half_inch, hAlign='LEFT'),
            P('<br/>'.join(ici_info), h4),
            P('MEMORANDUM', h2),
            T(info_table_data, style=info_table_style, hAlign='LEFT'),
            P('Please issue the following credit memo for account {} related to the invoice(s) listed below. '
              'Where applicable, this memo is to be broken down as follows:'.format(acct_no), h2),
            T(adj_table_info, style=adj_table_style, hAlign='LEFT'),
            P('Thank you<br/>{}, {}<br/>Dixon Optical Lab, 0562'.format(contact_name, ici.contact_title), h2),
        ]
        # Creating response
        response = HttpResponse(content_type='application/pdf')
        filename = 'Credit Request for {}.pdf'.format(acct_no)
        response['Content-Disposition'] = 'attachment; filename={}'.format(filename)
        credit_doc = SimpleDocTemplate(
            response,
            leftMargin=half_inch,
            rightMargin=half_inch,
            topMargin=half_inch,
            bottomMargin=half_inch,
            allowSplitting=True,
            pagesize=letter,
            title='Credit Request for {}'.format(acct_no),
        )
        credit_doc.build(story)
        return response


class MacolaRequestFormView(FormView):
    """
    View class that handles submission and generation of the MACOLA request form
    """
    form_class = bf.MacolaRequestForm
    template_name = 'billing/macola_request.html'
    initial = {'req_date': today, 'acct_type': 'Regular Account'}

    def get_form_kwargs(self):
        """
        Override building of form kwargs to account for url kwargs
        :return: dict
        """
        form_kwargs, acct_no = super(MacolaRequestFormView, self).get_form_kwargs(), self.kwargs['acct']
        form_kwargs.update(acct_no=None if acct_no == 'none' else acct_no)
        return form_kwargs
    
    def form_valid(self, form):
        """
        Handle validation of MACOLA request form
        :param form: Form instance
        :return: HttpResponse
        """
        # Initializing some general data
        cd, acct_no = form.cleaned_data, self.kwargs['acct']
        state_employee = cd['state_employee']
        if acct_no:
            acct_qset = MacolaAcct.objects.filter(account_No=acct_no)
            if not acct_qset.exists():
                msg = "Account '{}' does not exist in MACOLA Customer Accounts table".format(acct_no)
                form.add_error(None, msg)
                return self.form_invalid(form)
            acct = acct_qset.values().first()
            acct = dict(acct, tax_exemption='' if acct['tax_rate'] else acct['tax_exemption'])
        else:
            acct = dict(cd, account_No=acct_no)
        # Creating styling
        styles = getSampleStyleSheet()
        h1 = styles['Heading1']
        h1.alignment, black = 1, colors.black
        form_style = TableStyle([
            ('BOX', (0, 3), (-1, 3), 1, black),
            ('BOX', (0, 5), (-1, 6), 1, black),
            ('BOX', (0, 8), (-1, 16), 1, black),
            ('BOX', (0, -4), (-1, -1), 1, black),
            ('FONT', (0, 0), (-1, 5), 'Helvetica-Bold'),
            ('FONT', (0, 8), (0, 18), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -4), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        # Creating document data
        blank_row, tax_exempt = [''] * 2, acct['tax_exemption']
        address_info = itemgetter('inv_addr1', 'inv_addr2', 'inv_city', 'inv_State', 'inv_zip')(acct)
        table_data = [
            ['*Required Information', 'Macola Number Assigned: ____________________'],
            ['*Date of Request: {}'.format(cd['req_date'].strftime('%m/%d/%Y')),
             'Person Requesting Number: {}'.format(cd['req_person'])],
            blank_row,
            ['*Type of Account Requested: {}'.format(cd['acct_type']), ''],
            blank_row,
            # Sales Tax Status section
            ['*Sales Tax Status: {}'.format('Sales Tax Exempt' if tax_exempt else 'Taxable'),
             'Tax Exemption Number: {}'.format(tax_exempt)],
            ['++Note: A copy of Tax Exemption Certificate must be provided to Fiscal Department as '
             'proof in order for account to\nmaintain tax exemption status.', ''],
            blank_row,
            # Account Information section
            ['*Customer Information:', ''],
            ['Account Name:', '{} {}'.format(acct['name'], '#{}'.format(acct_no) if acct_no else '')],
            ['*Address:', '{}, {},\n{}, {} {}'.format(*address_info)],
            ['*Phone Number:', acct['phone']],
            ['*Fax Number:', acct['fax_No']],
            ['*Email Address:', acct['email']],
            ['*Account Holder {} a State Employee'.format('IS' if state_employee else 'is NOT'), ''],
        ]
        if state_employee:
            agency_fund = itemgetter('agency_no', 'fund_no')(cd)
            table_data.extend([
                ['State Agency Location: {}'.format(cd['agency_loc']), ''],
                ['Customer is a State Agency', 'Agency No: {} | Fund No: {}'.format(*agency_fund)],
            ])
        else:
            table_data.extend([[''] * 2] * 3)
        table_data.extend([
            blank_row,
            ['ICI Use Only', ''],
            ['Date Entered in Macola: __________', 'Entered By: __________'],
            ['Date NEW ACCOUNT SPREADSHEET Updated: _________', 'Account Number and Date Emailed: __________'],
            ['Revision Date: 5/15/2017'],
        ])
        # Creating story
        story = [
            P('New Macola Account Request Form', h1),
            T(table_data, style=form_style, colWidths=(3.5 * inch, 3.9 * inch), rowHeights=0.4 * inch),
        ]
        # Creating and returning response
        response = HttpResponse(content_type='application/pdf')
        filename = 'Macola Request for Account {}.pdf'.format(acct_no)
        response['Content-Disposition'] = 'attachment; filename={}'.format(filename)
        half_inch = 0.5 * inch
        req_form = SimpleDocTemplate(
            response,
            topMargin=half_inch,
            bottomMargin=half_inch,
            rightMargin=half_inch,
            leftMargin=half_inch,
            pagesize=letter,
            title='Macola Request Form',
        )
        req_form.build(story)
        return response


class CreditRequestFormView(FormView, BillingAppViewMixin):
    """
    View class that handles submission and generation of ICI Credit Request form
    """
    form_class = bf.CreditRequestForm
    template_name = 'billing/credit_request_form.html'
    initial = {'req_date': today, 'credit_no': 1}
    credit_formset = formset_factory(bf.CreditAdjustmentForm, extra=10)

    def get_context_data(self, **kwargs):
        """
        Update context dict to include formset
        :param kwargs: keyword args
        :return: dict
        """
        return super(CreditRequestFormView, self).get_context_data(**kwargs, formset=self.credit_formset)
    
    def form_valid(self, form):
        """
        Perform formset validation and generate credit request PDF file
        :param form: bound Form instance
        :return: HttpResponse
        """
        cd, credit_formset = form.cleaned_data, self.credit_formset
        credit_df = BillingInvoiceFormView.validate_form(credit_formset(self.request.POST))
        if credit_df is None:
            form.add_error(None, 'Must enter at least 1 adjustment')
            return self.form_invalid(form)
        if isinstance(credit_df, credit_formset):
            form.add_error(None, 'At least 1 adjustment form is incomplete')
            return self.form_invalid(form)
        reindexed_credit_df = credit_df.reindex_axis(['inv_no', 'sales', 'tax'], axis=1)
        reindexed_credit_df['total'] = credit_df.sales + credit_df.tax
        reindexed_credit_df['reason'] = credit_df.reason
        reindexed_credit_df['memo'] = ''
        [reindexed_credit_df.__setitem__(col, reindexed_credit_df[col].map(currency_or_blank))
         for col in ('sales', 'tax', 'total')]
        # Getting general info
        acct_no, contact_name = itemgetter('acct', 'req_person')(cd)
        ici, half_inch = BillingInvoiceFormView.get_ici_account(), 0.5 * inch
        # Creating styling objects
        normal, h2, h4 = itemgetter('Normal', 'Heading2', 'Heading4')(getSampleStyleSheet())
        h2.spaceBefore = half_inch
        h2.alignment, h4.alignment = 4, 2
        info_table_style = TableStyle([
            ('FONT', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 14),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        adj_table_style = TableStyle([
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGNMENT', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        ])
        # Creating data objects
        ici_info = [
            *attrgetter('name', 'inv_addr1', 'inv_addr2')(ici),
            '{}, {} {}'.format(*attrgetter('inv_city', 'inv_state', 'inv_zip')(ici)),
            'Phone: {} | Fax: {}'.format(*attrgetter('phone', 'fax_No')(ici)),
            'Email: {}'.format(ici.email),
            'Hours of Operation: M-F 6:00 am - 2:00 pm',
        ]
        info_table_data = [
            ['Date:', today.strftime('%B %d, %Y')],
            ['To:', 'Central Fiscal Office'],
            ['From:', contact_name],
            ['', 'Dixon Optical Lab (562)'],
            ['Subject:', 'Credit Memo Request CR-562-{}-{}'.format(today, cd['credit_no'])],
        ]
        adj_table_info = [['Invoice', 'Sales', 'Tax', 'Total', 'Reason', 'Memo No']]
        adj_table_info.extend(list(map(BillingInvoiceFormView.convert_to_currency, reindexed_credit_df.values)))
        # Creating story object
        story = [
            Image(self.get_ici_logo_path(), width=1.2 * inch, height=half_inch, hAlign='LEFT'),
            P('<br/>'.join(ici_info), h4),
            P('MEMORANDUM', h2),
            T(info_table_data, style=info_table_style, hAlign='LEFT'),
            P('Please issue the following credit memo for account {} related to the invoice(s) listed below. '
              'Where applicable, this memo is to be broken down as follows:'.format(acct_no), h2),
            T(adj_table_info, style=adj_table_style, hAlign='LEFT'),
            P('Thank you,<br/>{}<br/>Dixon Optical Lab, 562'.format(contact_name), h2),
        ]
        # Creating response
        response = HttpResponse(content_type='application/pdf')
        filename = 'Credit Request for {}.pdf'.format(acct_no)
        response['Content-Disposition'] = 'attachment; filename={}'.format(filename)
        credit_doc = SimpleDocTemplate(
            response,
            leftMargin=half_inch,
            rightMargin=half_inch,
            topMargin=half_inch,
            bottomMargin=half_inch,
            allowSplitting=True,
            pagesize=letter,
            title='Credit Request for {}'.format(acct_no),
        )
        credit_doc.build(story)
        return response
    
