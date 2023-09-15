from typing import TypeVar, Union
from urllib.parse import urlparse

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from odoorpc import ODOO

M = TypeVar("M", bound=models.Model)


class Company(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self) -> str:
        return self.name


class OdooConnection(models.Model):
    _rpc = None
    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name="odoo_connection"
    )
    url = models.CharField(max_length=255)

    def get_connection(self, url: Union[str, None] = None) -> ODOO:
        if not self._rpc:
            odoo_url = urlparse(url or self.url)
            self._rpc = ODOO(
                host=odoo_url.hostname, port=odoo_url.port, protocol=odoo_url.scheme
            )
            self._rpc.login(
                odoo_url.path[1:], login=odoo_url.username, password=odoo_url.password
            )
        return self._rpc


class OdooRecord(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="odoo_records"
    )
    odoo_id = models.PositiveIntegerField()
    odoo_model = models.CharField(max_length=255)

    django_id = models.CharField(max_length=255)
    django_model = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    django_object = GenericForeignKey("django_model", "django_id")

    last_sync_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "odoo_model", "odoo_id", "django_model"],
                name="unique_odoo_record",
            ),
            models.UniqueConstraint(
                fields=["django_model", "django_id"],
                name="unique_django_record",
            ),
        ]


class OdooRecordImage(models.Model):
    record = models.ForeignKey(OdooRecord, on_delete=models.CASCADE)
    odoo_field = models.CharField(max_length=255)
    django_field = models.CharField(max_length=255)
    image_hash = models.CharField(max_length=255)
    last_sync_at = models.DateTimeField(auto_now=True)
