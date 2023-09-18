# This file defines URL objects which map URLs to view functions.

from django.conf.urls import url
from . import views

app_name = 'billing'
urlpatterns = [
    url(r'^$', views.BillingInvoiceFormView.as_view(), name='invoice'),
    url(r'^generate_pdf/$', views.BillingInvoiceFormView.as_view(), name='genpdf'),
    url(r'^request_macola/(?P<acct>.*)/$', views.MacolaRequestFormView.as_view(), name='request_macola'),
    url(r'^credit_request/$', views.CreditRequestFormView.as_view(), name='credit_request'),
]