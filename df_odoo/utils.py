import math

from rest_framework.exceptions import ValidationError

from df_odoo.models import Customer


def get_active_session_id(db, cafe_o_id) -> int:
    session_ids = db.execute(
        "pos.session",
        "search",
        [("config_id", "=", cafe_o_id), ("state", "=", "opened")],
    )
    if not session_ids:
        raise ValidationError({"cafe_is_closed": "Cafe is closed"})
    return session_ids[0]


def get_partner_id(db, order):
    # Check customer exists in odoo
    customer, _ = Customer.objects.get_or_create(
        user=order.customer,
        o_company=order.o_company,
    )
    customer.o_update_or_create()

    data = db.execute(Customer.o_model, "read", [customer.o_id])
    partner_id, _ = data[0]["partner_id"]
    return partner_id


def create_sale_order(db, order):
    if not order.o_company.credit_product:
        raise ValidationError("This cafe temporarily does not accept online payment")

    product_id = db.execute(
        "product.product",
        "search",
        [("product_tmpl_id", "=", order.o_company.credit_product.o_id)],
    )[0]

    partner_id = get_partner_id(db, order)
    sale_order_id = db.execute(
        "sale.order",
        "create",
        [
            {
                "partner_id": partner_id,
            }
        ],
    )[0]
    db.execute(
        "sale.order.line",
        "create",
        [
            {
                "product_uom_qty": str(math.ceil(order.price_taxed_total)),
                "order_id": sale_order_id,
                "product_id": product_id,
            }
        ],
    )
    return sale_order_id


def check_need_new_tx(db, order):
    sale_order = db.execute("sale.order", "read", [order.o_id])[0]
    txs = db.execute("payment.transaction", "read", sale_order["transaction_ids"])
    return all((tx["state"] == "cancel" for tx in txs))


def create_stripe_session(db, order, stripe_return_url):
    return db.execute(
        "payment.acquirer",
        "stripe_create_checkout_session",
        [get_stripe_id(db)],
        {
            "order_id": order.sale_order_o_id,
            "success_url": stripe_return_url(success=True).format(order_id=order.id),
            "cancel_url": stripe_return_url(success=False).format(order_id=order.id),
        },
    )


def get_stripe_id(db):
    return db.execute("payment.acquirer", "search", [("provider", "=", "stripe")])[0]


def get_stripe_publishable_key(db):
    return db.execute("payment.acquirer", "read", [get_stripe_id(db)])[0][
        "stripe_publishable_key"
    ]


def create_pos_order(db, order):
    lines = order.lines.all()
    partner_id = get_partner_id(db, order)

    session_id = get_active_session_id(db, order.cafe.o_id)
    pos_reference = f"{session_id:05}-999-{order.id:04}"
    table = order.table or order.cafe.default_table

    if not order.o_id:
        order_data = {
            "partner_id": partner_id,
            "session_id": session_id,
            "config_id": order.cafe.o_id,
            "amount_tax": str(order.tax_total),
            "amount_total": str(order.price_taxed_total),
            "amount_paid": 0,
            "amount_return": 0,
            "customer_count": 1,
            "pos_reference": pos_reference,
        }

        if table:
            order_data["table_id"] = table.o_id

        order.pos_reference = pos_reference
        order.o_update_or_create(**order_data)

    # Sync order lines with odoo
    for line in lines:
        data = db.execute("product.template", "read", [line.product.o_id])
        product_id, _ = data[0]["product_variant_id"]
        line.o_update_or_create(
            order_id=order.o_id,
            product_id=product_id,
            price_subtotal=str(line.price_total),
            price_subtotal_incl=str(line.price_taxed_total),
            price_unit=str(line.price_unit),
        )

    db.execute_kw(
        "pos.config",
        "send_to_all_poses",
        [
            "table.order",
            {
                "table_order_display": {
                    "table_order_message": f"New order, table {table.title}",
                },
                "action": "update_table_order",
            },
        ],
    )


def check_sale_order_is_paid(db, sale_order_id):
    return db.execute("sale.order", "stripe_check_payment_status", [sale_order_id])


def postprocess_sale_order_tx(db, sale_order_id):
    return db.execute("sale.order", "stripe_postprocess_transactions", [sale_order_id])


#
# def sync_booked_resources(o_company):
#     db = o_company.o_db.connect()
#
#     ids = db.execute(
#         "resource.booking",
#         "search",
#         [("start", ">=", timezone.now().strftime("%Y-%m-%d"))],
#     )
#     existing_ids = UserTodo.objects.filter(
#         o_resource_booking_id__isnull=False
#     ).values_list("o_resource_booking_id", flat=True)
#     new_ids = list(set(ids) - set(existing_ids))
#     if not new_ids:
#         return
#
#     bookings = db.execute("resource.booking", "read", new_ids)
#     for booking in bookings:
#         partner = db.execute("res.partner", "read", booking["partner_id"][0])[0]
#
#         if not partner["user_ids"]:
#             continue
#
#         user_o_id = partner["user_ids"][0]
#         customer = Customer.objects.filter(o_company=o_company, o_id=user_o_id).first()
#         if not customer:
#             continue
#
#         cta_todo = UserTodo.objects.filter(
#             template__slug__startswith="cta_diagnostics",
#             user=customer.user,
#             is_done=False,
#         ).first()
#         if not cta_todo:
#             continue
#
#         cta_todo.is_done = True
#         cta_todo.save()
#
#         template = TodoTemplate.objects.filter(
#             slug=cta_todo.template.slug.replace("cta", "scheduled")
#         ).first()
#
#         if not template:
#             continue
#
#         UserTodo.objects.create(
#             template=template,
#             user=customer.user,
#             is_published=True,
#             event_datetime=booking["start"],
#             event_location=booking["location"] or "",
#             o_resource_booking_id=booking["id"],
#         )
