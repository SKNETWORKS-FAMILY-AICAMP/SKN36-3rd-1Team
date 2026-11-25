from django.urls import path, register_converter

from . import views
from .converters import PolicyIdConverter

register_converter(PolicyIdConverter, "policy_id")

app_name = "policies"

urlpatterns = [
    path("<policy_id:policy_id>/", views.policy_detail, name="detail"),
]
