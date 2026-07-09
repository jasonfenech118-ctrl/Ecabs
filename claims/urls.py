from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("claims/", views.claim_list, name="claim_list"),
    path("claims/new/", views.claim_new, name="claim_new"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/tab/<slug:tab>/", views.claim_detail, name="claim_tab"),
    path("claims/<int:pk>/edit/", views.claim_edit, name="claim_edit"),
    path("claims/<int:pk>/autosave/", views.claim_autosave, name="claim_autosave"),
    path("claims/<int:pk>/submit/", views.claim_submit, name="claim_submit"),
    path("claims/<int:pk>/status/", views.claim_set_status, name="claim_set_status"),
    path("claims/<int:pk>/photos/upload/", views.photo_upload, name="photo_upload"),
    path("claims/<int:pk>/surveys/add/", views.survey_add, name="survey_add"),
    path("claims/<int:pk>/emails/add/", views.email_add, name="email_add"),
    path("claims/<int:pk>/reminders/add/", views.reminder_add, name="reminder_add"),
    path("claims/<int:pk>/billing/add/", views.billing_add, name="billing_add"),
    path("reminders/", views.reminder_list, name="reminder_list"),
    path("reminders/<int:pk>/toggle/", views.reminder_toggle, name="reminder_toggle"),
]
