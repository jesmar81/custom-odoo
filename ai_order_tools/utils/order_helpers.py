import json

from odoo import _, fields
from odoo.exceptions import UserError
from odoo.tools import plaintext2html

EDITABLE_ORDER_STATES = ('draft', 'sent')
MAX_QUANTITY = 1_000_000
MAX_SEARCH_RESULTS = 10


def dump_json(payload):
    """Serialize a tool answer; the LLM reads it as plain text."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def to_record_id(raw_identifier, label):
    """LLMs send numbers as floats (12.0); accept them but reject anything that is not an id."""
    try:
        record_id = int(raw_identifier)
    except (TypeError, ValueError):
        raise UserError(_("%(label)s must be a numeric id.", label=label))
    if record_id <= 0 or record_id != raw_identifier:
        raise UserError(_("%(label)s must be a positive whole number.", label=label))
    return record_id


def browse_existing(model, raw_identifier, label):
    record = model.browse(to_record_id(raw_identifier, label)).exists()
    if not record:
        raise UserError(_("%(label)s %(identifier)s does not exist. Search for it again instead of guessing ids.",
                          label=label, identifier=raw_identifier))
    return record


def check_order_is_editable(order):
    if order.state not in EDITABLE_ORDER_STATES:
        raise UserError(_(
            "%(order)s is not a draft anymore (state: %(state)s). Only a person can change it.",
            order=order.display_name, state=order.state,
        ))


def check_quantity(quantity):
    if not isinstance(quantity, (int, float)) or not 0 < quantity <= MAX_QUANTITY:
        raise UserError(_("The quantity must be greater than 0 and at most %s.", MAX_QUANTITY))
    return quantity


def check_price(price_unit):
    if not isinstance(price_unit, (int, float)) or price_unit < 0:
        raise UserError(_("The unit price cannot be negative."))
    return price_unit


def check_discount(discount):
    if not isinstance(discount, (int, float)) or not 0 <= discount <= 100:
        raise UserError(_("The discount must be a percentage between 0 and 100."))
    return discount


def parse_date(raw_date, label):
    try:
        parsed_date = fields.Date.from_string(raw_date)
    except (TypeError, ValueError):
        parsed_date = None
    if not parsed_date:
        raise UserError(_("%(label)s must be a date formatted as YYYY-MM-DD.", label=label))
    return parsed_date


def text_to_html(plain_text):
    return plaintext2html(plain_text)


def get_record_url(record):
    return f"{record.get_base_url()}/odoo/{record._name}/{record.id}"
