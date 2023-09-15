from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from df_odoo.logic import sync_all_models_records, sync_all_models_to_odoo
from df_odoo.models import OdooConnection, OdooRecord, OdooRecordImage


@admin.register(OdooConnection)
class OdooConnectionAdmin(admin.ModelAdmin):
    list_display = ("company", "url")

    def sync_all_models(
        self, request: HttpRequest, queryset: QuerySet[OdooConnection]
    ) -> None:
        for connection in queryset:
            sync_all_models_records(connection)

    def sync_models_to_odoo(
        self, request: HttpRequest, queryset: QuerySet[OdooConnection]
    ) -> None:
        for connection in queryset:
            sync_all_models_to_odoo(connection)

    actions = (
        sync_all_models,
        sync_models_to_odoo,
    )


@admin.register(OdooRecord)
class OdooRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "odoo_id",
        "odoo_model",
        "django_model",
        "django_id",
    )


@admin.register(OdooRecordImage)
class OdooRecordImageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "record_id",
        "image_hash",
        "odoo_field",
        "django_field",
        "last_sync_at",
    )
