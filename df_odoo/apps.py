from django.apps import AppConfig


class OdooConfig(AppConfig):
    default_auto_field = "hashid_field.BigHashidAutoField"
    name = "df_odoo"
    api_path = "odoo/"
    verbose_name = "Odoo Integration"
