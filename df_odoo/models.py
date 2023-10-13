from __future__ import annotations

import base64
import hashlib
from itertools import chain
from typing import Optional
from uuid import uuid4

import odoorpc
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import CharField, TextField
from django.utils import timezone
from environ import urlparse


def _format_value(field, value):
    if isinstance(field, (TextField, CharField)) and value is False:
        # Odoo returns False for empty strings
        return ""

    if isinstance(value, list) and isinstance(value[0], int):
        # Extract instance string value from odoo foreign key
        return value[1]

    return value


class OdooQuerySet(models.QuerySet):
    def search(self, db, **kwargs):
        return db.env[self.model.o_model].search(kwargs)

    def create_from_search(self, db, **kwargs):
        for o in self.search(db, **kwargs):
            print(vars(o))

    def load_odoo_to_django(self, company: Company):  # noqa: C901
        if not self.model.o_field_map:
            raise RuntimeError(
                f"you need to specify `o_field_map` for the {self.model._meta.label} model"
            )

        # Fetch odoo data
        db = company.o_db.connect()
        odoo_ids = db.execute(self.model.o_model, "search", [])
        odoo_models = db.execute(
            self.model.o_model,
            "read",
            odoo_ids,
            list(self.model.o_field_map.keys())
            + list(self.model.o_fk_field_map.keys())
            + list(self.model.o_m2m_field_map.keys()),
        )

        # Fetch odoo_id -> django_id mapping for related objects
        odoo_to_django_ids = {}
        for o_field, d_field in chain(
            self.model.o_m2m_field_map.items(), self.model.o_fk_field_map.items()
        ):
            field = getattr(self.model, d_field)
            model = field.field.related_model
            if getattr(field, "reverse", None) is True:
                model = field.field.model

            odoo_to_django_ids[o_field] = {
                item[0]: item[1]
                for item in model.objects.filter(
                    o_company=company, o_id__isnull=False
                ).values_list("o_id", "id")
            }

        for odoo_model in odoo_models:
            # Set regular fields
            fields = {}
            for o_field, d_field in self.model.o_field_map.items():
                fields[d_field] = _format_value(
                    getattr(self.model, d_field).field, odoo_model[o_field]
                )

            # Set fk fields
            for o_field, d_field in self.model.o_fk_field_map.items():
                o_id = odoo_model[o_field]
                if o_id is False:
                    o_id = None
                elif isinstance(o_id, list):
                    o_id = o_id[0]
                d_id = odoo_to_django_ids[o_field].get(o_id)
                fields[f"{d_field}_id"] = d_id

            # Set defaults
            for field, value in self.model.o_defaults.items():
                if callable(value):
                    value = value(company)
                fields[field] = value

            # Create/update an instance
            instance, _ = self.model.objects.update_or_create(
                o_id=odoo_model["id"],
                o_company=company,
                defaults=fields,
            )

            # Set m2m fields
            for o_field, d_field in self.model.o_m2m_field_map.items():
                o_ids = odoo_model[o_field]
                if o_ids is False:
                    o_ids = []
                d_ids = [
                    odoo_to_django_ids[o_field].get(o_id)
                    for o_id in o_ids
                    if odoo_to_django_ids[o_field].get(o_id)
                ]
                getattr(instance, d_field).set(d_ids)

    def load_odoo_images(self, company: Company):
        if not self.model.o_image_field or not self.model.d_image_field:
            raise RuntimeError(
                f"you need to specify `o_image_field` and `d_image_field` "
                f"for the {self.model._meta.label} model"
            )

        for instance in self.model.objects.filter(o_company=company).all():
            if not instance.o_id:
                # Instance isn't synced with odoo
                continue

            db = company.o_db.connect()
            data = db.execute(
                self.model.o_model, "read", [instance.o_id], [self.model.o_image_field]
            )

            image_content = data[0].get(self.model.o_image_field, None)
            if not image_content:
                # No image in odoo
                continue

            image_bytes = base64.b64decode(image_content)
            image_hash = hashlib.md5(image_bytes).hexdigest()  # noqa: S324

            if instance.o_image_hash == image_hash:
                # We alreary loaded this image earlier
                continue

            image_field = getattr(instance, self.model.d_image_field)
            image_field.save("image.jpg", ContentFile(image_bytes))
            instance.o_image_hash = image_hash
            instance.save()


class Connection(models.Model):
    _rpc = None
    url = models.CharField(max_length=256)

    @property
    def env(self, *args, **kwargs):
        self.connect()
        return self._rpc.env(*args, **kwargs)

    def connect(self):
        if not self._rpc:
            odoo_url = urlparse(self.url)
            self._rpc = odoorpc.ODOO(
                host=odoo_url.hostname, port=odoo_url.port, protocol=odoo_url.scheme
            )
        if not self._rpc._login:
            self._rpc.login(
                odoo_url.path[1:], login=odoo_url.username, password=odoo_url.password
            )
        return self._rpc

    def __str__(self):
        return str(self.id)


class OdooMixin(models.Model):
    o_id = models.PositiveIntegerField(null=True, blank=True)
    o_db = models.ForeignKey(Connection, on_delete=models.CASCADE)
    o_updated = models.DateTimeField(null=True, blank=True)
    # a map of odoo field : django model field
    o_field_map = {}
    o_fk_field_map = {}
    o_m2m_field_map = {}
    o_model: str
    # o_kwargs defaults
    o_defaults = {}
    o_create_defaults = {}
    o_create_context = {}
    o_search_kwargs = {}
    instance_field_name = "instance"  # or 'self'

    # Name of image field in Odoo
    o_image_field: Optional[str] = None
    # Name of image field in Django
    d_image_field: Optional[str] = None
    # We use this hash for not loading images twice for sync with odoo
    o_image_hash = models.CharField(
        max_length=32, null=True, editable=False, db_index=True
    )

    objects = OdooQuerySet.as_manager()

    def o_search_id(self):
        """
        You can override this method to avoid creating duplicate records in Odoo.

        :return: Searches for a unique record in Odoo representing this model object
        """
        return self.o_id

    @property
    def o_ref(self):
        """
        :return: External odoo reference for this object
        """
        return f"hlc_{self._meta.model_name}.{self.id}"

    @property
    def o_kwargs(self):
        """

        :return: default kwargs for update/create
        """
        return {
            **self.o_defaults,
            **{k: getattr(self, v) for k, v in self.o_field_map.items()},
        }

    def o_update_or_create(self, db=None, **kwargs):
        """
        Updates the Odoo record with the Django data

        :param db:
        :param kwargs:
        :return:
        """
        db = db or self.o_db
        kwargs = {**self.o_kwargs, **kwargs}
        model = db.env[self.o_model]

        self.o_id = self.o_id or self.o_search_id()

        if not self.o_id:
            kwargs = {**self.o_create_defaults, **kwargs}
            self.o_id = (
                model.with_context(self.o_create_context).create(kwargs)
                if self.o_create_context
                else model.create(kwargs)
            )
            # if self.o_ref:
            # db.env["ir.model.data"].create(
            #     {
            #         "model": self.o_model,
            #         "name": self.o_ref,
            #         "res_id": self.o_id,
            #     }
            # )
        else:
            record = model.browse([self.o_id])[0]
            record.write(kwargs)
        self.o_updated = timezone.now()
        self.save()

    def o_create(self, db=None, **kwargs):
        if self.o_id:
            raise ValueError(f"{self.__str__()}: already has an o_id {self.odoo_id}")
        return self.o_update_or_create(db=db, **kwargs)

    def o_load(self, db=None, o_id=None):
        """
        Load data from odoo
        :param db:
        :param o_id:
        :return:
        """
        record = self.o_retrieve(db=db, o_id=o_id)
        for k, v in self.o_field_map.items():
            setattr(self, v, getattr(record, k))
        self.save()

    def o_retrieve(self, db=None, o_id=None):
        """
        Retrieves Odoo record but does not save it in Django.
        :param db:
        :param o_id:
        :return: Odoo data
        """
        o_id = o_id or self.o_id
        db = db or self.o_db
        if not o_id:
            raise ValueError(f"{self.__str__()}: can not retrieve record without o_id")

        model = db.env[self.o_model]
        return model.browse([self.o_id])[0]

    class Meta:
        abstract = True


class Company(OdooMixin):
    o_model = "res.company"
    o_field_map = {"website": "website_url", "city": "city", "street": "street"}

    name = models.CharField(max_length=64, blank=True, default="")
    city = models.CharField(max_length=64, blank=True, default="")
    street = models.CharField(max_length=128, blank=True, default="")

    slug = models.CharField(max_length=16, unique=True)
    website_url = models.URLField()
    credit_product = models.ForeignKey("cafes.Product", models.SET_NULL, null=True)

    def login_user(self, user, redirect):
        customer = Customer.objects.get_or_create(user=user, o_company=self)[0]
        customer.o_update_or_create()
        token = customer.o_login()
        return (
            f"{self.website_url}/sso/redirect?token={token}&redirect={redirect or '/'}"
        )

    def __str__(self):
        return self.slug

    class Meta:
        verbose_name_plural = "companies"


class OdooCompanyModelMixin(OdooMixin):
    o_company = models.ForeignKey(Company, on_delete=models.CASCADE)

    @property
    def o_db(self):
        return self.o_company.o_db

    @property
    def o_kwargs(self):
        return {"company_id": self.o_company.o_id, **super().o_kwargs}

    class Meta:
        abstract = True

    def __str__(self):
        return f"{getattr(self, 'title', '')} ({self.o_company_id})"


class Customer(OdooCompanyModelMixin):
    o_model = "res.users"
    o_field_map = {"name": "name", "login": "email", "email": "email"}
    o_create_defaults = {"sel_groups_1_8_9": 9, "active": True}
    o_create_context = {"no_reset_password": True}
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    @property
    def name(self):
        return f"{self.user.first_name} {self.user.last_name}"

    @property
    def email(self):
        return self.user.email

    def o_login(self):
        key = str(uuid4())
        record = self.o_retrieve()
        record.write({"sso_key": key})
        return key

    def o_search_id(self):
        ids = self.o_db.env[self.o_model].search(
            [("email", "=", self.user.email)], limit=1
        )
        return ids[0] if ids else None

    def __str__(self):
        return f"[{self.o_company.slug}] {self.user.email}"
