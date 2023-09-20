import base64
import hashlib
import io
from dataclasses import dataclass, field
from itertools import chain
from typing import Any, Callable, Dict, Optional, Type, Union

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import CharField, TextField
from odoorpc import ODOO
from PIL import Image

from df_odoo.models import (
    Company,
    M,
    OdooConnection,
    OdooRecord,
    OdooRecordImage,
)


def get_model_from_str(model_str: str) -> Any:
    app_label, model_name = model_str.split(".")
    return apps.get_model(app_label, model_name)


def base64_to_sha256(base64_str: str) -> str:
    bytes_data = base64.b64decode(base64_str)
    sha256_hash = hashlib.sha256(bytes_data).hexdigest()
    return sha256_hash


def _format_value(field: str, value: str) -> str:
    if isinstance(field, (TextField, CharField)) and value is False:
        # Odoo returns False for empty strings
        return ""

    if isinstance(value, list) and isinstance(value[0], int):
        # Extract instance string value from odoo foreign key
        return value[1]

    return value


@dataclass
class OdooModelMapping:
    odoo_model: str
    django_model: str
    flat_field_mapping: Dict[str, str] = field(default_factory=dict)
    fk_field_mapping: Dict[str, str] = field(default_factory=dict)
    m2m_field_mapping: Dict[str, str] = field(default_factory=dict)
    defaults: Dict[str, Union[Callable[[Company], Any], Any]] = field(
        default_factory=dict
    )
    image_field_mapping: Dict[str, str] = field(default_factory=dict)
    callable_mapping: Dict[str, Callable] = field(default_factory=dict)


RESTAURANT_MAPPING = OdooModelMapping(
    odoo_model="pos.config",
    django_model="restaurants.Restaurant",
    flat_field_mapping={
        "name": "name",
    },
    image_field_mapping={"iface_display_categ_images": "image"},
)
FLOOR_MAPPING = OdooModelMapping(
    odoo_model="restaurant.floor",
    django_model="restaurants.Floor",
    flat_field_mapping={
        "name": "name",
        "sequence": "sequence",
    },
    fk_field_mapping={
        "pos_config_id": "restaurant",
    },
    image_field_mapping={"background_image": "image"},
)
TABLE_MAPPING = OdooModelMapping(
    odoo_model="restaurant.table",
    django_model="restaurants.Table",
    flat_field_mapping={
        "name": "name",
        "active": "is_active",
        "seats": "seats",
    },
    fk_field_mapping={
        "floor_id": "floor",
    },
)
CATEGORY_MAPPING = OdooModelMapping(
    odoo_model="pos.category",
    django_model="restaurants.Category",
    flat_field_mapping={
        "name": "name",
        "sequence": "sequence",
    },
    image_field_mapping={"image_128": "image"},
)
PRODUCT_MAPPING = OdooModelMapping(
    odoo_model="product.template",
    django_model="products.Product",
    flat_field_mapping={
        "name": "name",
        "description": "description",
        "sequence": "sequence",
        "available_in_pos": "is_available",
    },
    m2m_field_mapping={
        "pos_categ_id": "categories",
    },
    image_field_mapping={"image_128": "image"},
)

TAX_MAPPING = OdooModelMapping(
    odoo_model="account.tax",
    django_model="products.Tax",
    flat_field_mapping={
        "amount": "amount",
        "name": "name",
        "description": "description",
        "sequence": "sequence",
    },
)

CUSTOMER_MAPPING = OdooModelMapping(
    odoo_model="res.users",
    django_model="accounts.Customer",
    defaults={"active": True, "sel_groups_1_8_9": 9},
    callable_mapping={
        "email": lambda customer: customer.user.email,
        "login": lambda customer: customer.user.email,
        "name": lambda customer: customer.user.get_full_name(),
    },
)


"""
class ProductPrice(OdooCompanyModelMixin):
    o_model = "product.template"
    o_field_map = {
        "list_price": "price",
    }
    o_defaults = {"pricelist": lambda c: Pricelist.objects.of_company(c)}
    o_fk_field_map = {
        "id": "product",
    }
    o_m2m_field_map = {
        "taxes_id": "taxes",
    }"""


def sync_model_records(
    connection: OdooConnection, schema: OdooModelMapping
) -> None:  # noqa: C901
    # TODO: delete all records that are not in odoo anymore
    db = connection.get_connection()
    django_model = get_model_from_str(schema.django_model)

    odoo_records = {
        record.odoo_id: record
        for record in OdooRecord.objects.filter(
            company=connection.company,
            odoo_model=schema.odoo_model,
            django_model=ContentType.objects.get_for_model(django_model),
        ).all()
    }
    odoo_ids = db.execute(schema.odoo_model, "search", [])

    odoo_instances = db.execute(
        schema.odoo_model,
        "read",
        odoo_ids,
        list(schema.flat_field_mapping.keys())
        + list(schema.fk_field_mapping.keys())
        + list(schema.m2m_field_mapping.keys()),
    )

    django_instances = {
        str(instance.id): instance
        for instance in django_model.objects.filter(company=connection.company).all()
    }

    # Fetch odoo_id -> django_id mapping for related objects
    odoo_to_django_ids = {}
    for o_field, d_field in chain(
        schema.m2m_field_mapping.items(), schema.fk_field_mapping.items()
    ):
        field = getattr(django_model, d_field)
        model = field.field.related_model
        if getattr(field, "reverse", None) is True:
            model = field.field.model

        odoo_to_django_ids[o_field] = {
            item[0]: item[1]
            for item in OdooRecord.objects.filter(
                company=connection.company,
                django_model=ContentType.objects.get_for_model(model),
            ).values_list("odoo_id", "django_id")
        }

    for odoo_instance in odoo_instances:
        # Set regular fields
        fields: Dict[str, Any] = {}
        for o_field, d_field in schema.flat_field_mapping.items():
            fields[d_field] = _format_value(
                getattr(django_model, d_field).field, odoo_instance[o_field]
            )

        # Set fk fields
        for o_field, d_field in schema.fk_field_mapping.items():
            o_id = odoo_instance[o_field]
            if o_id is False:
                o_id = None
            elif isinstance(o_id, list):
                o_id = o_id[0]
            d_id = odoo_to_django_ids[o_field].get(o_id)
            fields[f"{d_field}_id"] = d_id

        # Set defaults
        for field, value in schema.defaults.items():
            if callable(value):
                value = value(connection.company)
            fields[field] = value

        with transaction.atomic():
            odoo_record = odoo_records.get(odoo_instance["id"])
            if not odoo_record:
                django_instance = django_model.objects.create(
                    company=connection.company,
                    **fields,
                )
                django_instances[str(django_instance.id)] = django_instance
                odoo_record = OdooRecord.objects.create(
                    company=connection.company,
                    odoo_id=odoo_instance["id"],
                    odoo_model=schema.odoo_model,
                    django_id=str(django_instance.id),
                    django_model=ContentType.objects.get_for_model(django_model),
                )
                odoo_records[odoo_instance["id"]] = odoo_record
            else:
                django_instance = django_instances[odoo_record.django_id]
                for field, value in fields.items():
                    setattr(django_instance, field, value)
                django_instance.save()
                odoo_record.save()

            # Set m2m fields
            for o_field, d_field in schema.m2m_field_mapping.items():
                o_ids = odoo_instance[o_field]
                if o_ids is False:
                    o_ids = []
                d_ids = [
                    odoo_to_django_ids[o_field].get(o_id)
                    for o_id in o_ids
                    if odoo_to_django_ids[o_field].get(o_id)
                ]
                getattr(django_instance, d_field).set(d_ids)


def sync_model_image_records(
    connection: OdooConnection, schema: OdooModelMapping
) -> None:
    db = connection.get_connection()
    django_model = get_model_from_str(schema.django_model)

    odoo_records = {
        record.odoo_id: record
        for record in OdooRecord.objects.filter(
            company=connection.company,
            odoo_model=schema.odoo_model,
            django_model=ContentType.objects.get_for_model(django_model),
        ).all()
    }
    odoo_ids = db.execute(schema.odoo_model, "search", [])
    odoo_instances = db.execute(
        schema.odoo_model,
        "read",
        odoo_ids,
        list(schema.image_field_mapping.keys()),
    )

    django_instances = {
        str(instance.id): instance
        for instance in django_model.objects.filter(company=connection.company).all()
    }

    for odoo_instance in odoo_instances:
        # Set regular fields
        fields = {}
        for o_field, d_field in schema.image_field_mapping.items():
            fields[d_field] = {
                "django_field": _format_value(
                    getattr(django_model, d_field).field, odoo_instance[o_field]
                ),
                "odoo_field": o_field,
            }

        with transaction.atomic():
            odoo_record = odoo_records.get(odoo_instance["id"])
            if odoo_record:
                django_instance = django_instances[odoo_record.django_id]
                for d_field, value in fields.items():
                    django_field = value["django_field"]

                    if django_field:
                        base64_image = django_field
                        binary_date = base64.b64decode(base64_image)
                        image_format = Image.open(io.BytesIO(binary_date)).format
                        image_hash = base64_to_sha256(base64_image)

                        (
                            odoo_record_image,
                            created,
                        ) = OdooRecordImage.objects.get_or_create(
                            record=odoo_record,
                            odoo_field=value["odoo_field"],
                            django_field=d_field,
                        )
                        if created or image_hash != odoo_record_image.image_hash:
                            odoo_record_image.image_hash = image_hash
                            odoo_record_image.save()
                            getattr(django_instance, d_field).save(
                                f"image.{image_format.lower()}",
                                ContentFile(binary_date),
                            )

                django_instance.save()


def sync_single_model_from_django_to_odoo(
    db: ODOO,
    company: Company,
    schema: OdooModelMapping,
    obj: Optional[Type[M]] = None,
    raw_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    final_data = {}
    django_id = None
    if obj:
        django_id = obj.id
        # for default fields
        for o_field, d_field in schema.defaults.items():
            if callable(d_field):
                final_data[o_field] = d_field(obj)  # type: ignore[arg-type, union-attr]
            else:
                final_data[o_field] = d_field

        # for callable fields
        for o_field, d_field in schema.callable_mapping.items():
            if callable(d_field):
                final_data[o_field] = d_field(obj)

        for o_field, d_field in schema.flat_field_mapping.items():
            final_data[o_field] = _format_value(d_field, getattr(obj, d_field))
    else:
        django_id = raw_data.pop("django_id")  # type: ignore[arg-type, union-attr]
        final_data = raw_data  # type: ignore[assignment]

    with transaction.atomic():
        odoo_id = db.execute(
            schema.odoo_model,
            "create",
            [final_data],
        )[0]
        OdooRecord.objects.create(
            company=company,
            odoo_id=odoo_id,
            odoo_model=schema.odoo_model,
            django_model=ContentType.objects.get_for_model(
                get_model_from_str(schema.django_model)
            ),
            django_id=str(django_id),
        )
        return odoo_id


def sync_django_models_to_odoo(
    connection: OdooConnection, schema: OdooModelMapping
) -> None:
    django_model = get_model_from_str(schema.django_model)
    odoo_records = OdooRecord.objects.filter(
        company=connection.company,
        odoo_model=schema.odoo_model,
        django_model=ContentType.objects.get_for_model(django_model),
    ).all()

    d_models = django_model.objects.exclude(
        id__in=list(odoo_records.values_list("django_id", flat=True))
    )

    db = connection.get_connection()
    for d_model in d_models:
        sync_single_model_from_django_to_odoo(
            db=db, company=connection.company, schema=schema, obj=d_model
        )


def sync_all_models_records(connection: OdooConnection) -> None:
    mappings = [
        RESTAURANT_MAPPING,
        FLOOR_MAPPING,
        TABLE_MAPPING,
        CATEGORY_MAPPING,
        PRODUCT_MAPPING,
        TAX_MAPPING,
    ]
    for schema in mappings:
        sync_model_records(connection, schema)

    image_mappings = [PRODUCT_MAPPING]
    for schema in image_mappings:
        sync_model_image_records(connection, schema)


def sync_all_models_to_odoo(connection: OdooConnection) -> None:
    mappings = [
        CUSTOMER_MAPPING,
    ]
    for mapping in mappings:
        sync_django_models_to_odoo(connection, mapping)
