from django.contrib import admin

from .models import (
    Company,
    Connection,
    Customer,
)


class OdooModelAdmin(admin.ModelAdmin):
    @admin.action(description="Load data from odoo")
    def o_load(self, request, queryset):
        for o in queryset:
            o.o_load()

    @admin.action(description="Update data in odoo")
    def o_update(self, request, queryset):
        for o in queryset:
            o.o_update_or_create()

    actions = [o_load, o_update]


@admin.register(Connection)
class ConnectionAdmin(OdooModelAdmin):
    list_display = ("id", "url")
    search_fields = ("cafe__slug", "url")


@admin.register(Company)
class CompanyAdmin(OdooModelAdmin):
    list_display = ("slug", "website_url", "o_updated")
    autocomplete_fields = ("credit_product",)


@admin.register(Customer)
class CustomerAdmin(OdooModelAdmin):
    list_display = ("o_company", "user", "o_updated")
    search_fields = ("user__email",)
    list_filter = ("o_company__slug",)
