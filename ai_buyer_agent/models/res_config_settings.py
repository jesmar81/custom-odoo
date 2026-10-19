from odoo import fields, models

DEFAULT_WHATSAPP_REQUEST_TEXT = (
    "Hola {vendor}, le saluda {company}. ¿Nos puede cotizar {quantity} {uom} de {product}"
    " para el {needed_date}? Por favor indíquenos precio unitario, tiempo de entrega y condiciones de pago."
    " Referencia: {reference}. Gracias."
)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_buyer_wait_hours = fields.Integer(
        string="Hours to wait for quotations",
        config_parameter='ai_buyer_agent.wait_hours',
        default=24,
        groups='purchase.group_purchase_manager',
    )
    ai_buyer_history_days = fields.Integer(
        string="Purchase history considered (days)",
        config_parameter='ai_buyer_agent.history_days',
        default=730,
        groups='purchase.group_purchase_manager',
    )
    ai_buyer_whatsapp_text = fields.Text(
        string="WhatsApp request text",
        config_parameter='ai_buyer_agent.whatsapp_text',
        default=DEFAULT_WHATSAPP_REQUEST_TEXT,
        groups='purchase.group_purchase_manager',
        help="Placeholders: {vendor} {company} {quantity} {uom} {product} {needed_date} {reference}",
    )
