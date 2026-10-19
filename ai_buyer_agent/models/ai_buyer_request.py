import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext, html_escape, plaintext2html

from odoo.addons.ai_order_tools.utils.order_helpers import dump_json, parse_date

from .res_config_settings import DEFAULT_WHATSAPP_REQUEST_TEXT

_logger = logging.getLogger(__name__)

QUOTES_REQUESTED = 3
DEFAULT_WAIT_HOURS = 24
DEFAULT_HISTORY_DAYS = 730
MAX_REPLIES_PER_ORDER = 5
MAX_REPLY_LENGTH = 1500
OPEN_STATES = ('to_approve', 'waiting', 'ready')
RFQ_EMAIL_TEMPLATE = 'purchase.email_template_edi_purchase'
EMAIL_LAYOUT = 'mail.mail_notification_layout_with_responsible_signature'


class AiBuyerRequest(models.Model):
    _name = 'ai.buyer.request'
    _description = "AI Buyer Request"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True, readonly=True)
    user_id = fields.Many2one('res.users', string="Requested by", default=lambda self: self.env.user, readonly=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True)
    product_qty = fields.Float(string="Quantity", required=True, readonly=True, digits='Product Unit')
    product_uom_id = fields.Many2one('uom.uom', related='product_id.uom_id', string="Unit")
    needed_date = fields.Date(string="Needed by", readonly=True)
    note = fields.Text(readonly=True)
    state = fields.Selection(
        [
            ('to_approve', "To approve"),
            ('waiting', "Waiting for quotations"),
            ('ready', "Ready to compare"),
            ('done', "Done"),
            ('manual', "Manual (no eligible vendor)"),
            ('cancel', "Cancelled"),
        ],
        default='to_approve', required=True, tracking=True, copy=False,
    )
    order_ids = fields.One2many('purchase.order', 'ai_buyer_request_id', string="Requests for Quotation")
    skipped_partner_ids = fields.Many2many(
        'res.partner', string="Vendors handled manually",
        help="Vendors from the history that are bought through an intermediary. The AI never contacts them.",
    )
    sent_date = fields.Datetime(readonly=True, copy=False)
    deadline_date = fields.Datetime(string="Reply deadline", readonly=True, copy=False)
    recommendation = fields.Html(readonly=True, copy=False)
    comparison_html = fields.Html(compute='_compute_comparison_html', sanitize=False)

    # ------------------------------------------------------------
    # CONFIGURATION
    # ------------------------------------------------------------

    @api.model
    def _get_wait_hours(self):
        return int(self.env['ir.config_parameter'].sudo().get_param('ai_buyer_agent.wait_hours') or DEFAULT_WAIT_HOURS)

    @api.model
    def _get_history_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param('ai_buyer_agent.history_days') or DEFAULT_HISTORY_DAYS)

    # ------------------------------------------------------------
    # VENDOR SELECTION
    # ------------------------------------------------------------

    @api.model
    def _select_vendors(self, product):
        """Pick up to 3 vendors from the purchase history.

        Ranked by number of purchases of the product, then by recency. Vendors flagged
        "buy through an intermediary" are excluded and reported separately; vendors
        without phone and email cannot be contacted and are left out as well.

        :return: (selected vendors, flagged vendors, unreachable vendors)
        """
        history_start = fields.Datetime.now() - timedelta(days=self._get_history_days())
        past_orders = self.env['purchase.order'].search([
            ('order_line.product_id', '=', product.id),
            ('state', 'in', ('purchase', 'done')),
            ('date_order', '>=', history_start),
        ])
        purchase_count = defaultdict(int)
        last_purchase = {}
        for order in past_orders:
            purchase_count[order.partner_id] += 1
            last_purchase[order.partner_id] = max(last_purchase.get(order.partner_id, order.date_order), order.date_order)
        ranked_vendors = sorted(
            purchase_count, key=lambda vendor: (-purchase_count[vendor], -last_purchase[vendor].timestamp()),
        )
        flagged = self.env['res.partner']
        unreachable = self.env['res.partner']
        selected = self.env['res.partner']
        for vendor in ranked_vendors:
            if vendor.buy_through_intermediary:
                flagged |= vendor
            elif not vendor.active or not (vendor.phone or vendor.email):
                unreachable |= vendor
            elif len(selected) < QUOTES_REQUESTED:
                selected |= vendor
        return selected, flagged, unreachable

    # ------------------------------------------------------------
    # CREATION (AI tool)
    # ------------------------------------------------------------

    @api.model
    def _ai_start_request(self, product_id, quantity, needed_date=None, note=None):
        product = self.env['product.product'].browse(int(product_id)).exists()
        if not product or not product.purchase_ok:
            raise UserError(_("The product does not exist or cannot be purchased."))
        if not isinstance(quantity, (int, float)) or quantity <= 0:
            raise UserError(_("The quantity must be greater than 0."))
        if needed_date is not None:
            needed_date = parse_date(needed_date, _("The needed date"))
        open_request = self.search([('product_id', '=', product.id), ('state', 'in', OPEN_STATES)], limit=1)
        if open_request:
            raise UserError(_(
                "There is already an open request for %(product)s: %(request)s (%(state)s). Use it instead of creating another.",
                product=product.display_name, request=open_request.name, state=open_request.state,
            ))

        selected, flagged, unreachable = self._select_vendors(product)
        request = self.create({
            'name': self.env['ir.sequence'].next_by_code('ai.buyer.request') or _("New"),
            'product_id': product.id,
            'product_qty': quantity,
            'needed_date': needed_date,
            'note': note,
            'state': 'to_approve' if selected else 'manual',
            'skipped_partner_ids': flagged.ids,
        })
        if not selected:
            request.message_post(body=_(
                "No vendor can be asked automatically (%(flagged)s vendor(s) handled manually, %(unreachable)s without contact data). "
                "A person must handle this purchase.", flagged=len(flagged), unreachable=len(unreachable),
            ))
            return dump_json(request._ai_summary())

        orders = self.env['purchase.order']
        for vendor in selected:
            orders |= self.env['purchase.order'].create({
                'partner_id': vendor.id,
                'ai_buyer_request_id': request.id,
                'order_line': [(0, 0, {'product_id': product.id, 'product_qty': quantity})],
                **({'date_planned': needed_date} if needed_date else {}),
            })
        if len(orders) > 1:
            group = self.env['purchase.order.group'].create({})
            orders.write({'purchase_group_id': group.id})
        request.message_post(body=_(
            "Draft requests for quotation prepared for: %(vendors)s. Nothing was sent: a person must approve.",
            vendors=", ".join(selected.mapped('display_name')),
        ))
        request.activity_schedule(
            'mail.mail_activity_data_todo', user_id=request.user_id.id,
            summary=_("Review and approve the requests for quotation"),
        )
        return dump_json(request._ai_summary())

    # ------------------------------------------------------------
    # APPROVAL AND SENDING (human only)
    # ------------------------------------------------------------

    def _get_whatsapp_text(self, order):
        self.ensure_one()
        placeholders = defaultdict(str, {
            'vendor': order.partner_id.name,
            'company': order.company_id.name,
            'quantity': f"{self.product_qty:g}",
            'uom': self.product_uom_id.name,
            'product': self.product_id.display_name,
            'needed_date': self.needed_date.strftime('%d/%m/%Y') if self.needed_date else _("the earliest date"),
            'reference': order.name,
        })
        configured_text = self.env['ir.config_parameter'].sudo().get_param('ai_buyer_agent.whatsapp_text')
        try:
            return (configured_text or DEFAULT_WHATSAPP_REQUEST_TEXT).format_map(placeholders)
        except (ValueError, IndexError):  # malformed braces typed in the settings
            return DEFAULT_WHATSAPP_REQUEST_TEXT.format_map(placeholders)

    def _send_order(self, order):
        """Send one request through WhatsApp (Evolution API) or email.

        :return: description of the channel used, or False when nothing could be sent
        """
        self.ensure_one()
        vendor = order.partner_id
        evolution_enabled = bool(self.env['evolution.message']._get_settings()['enabled'])
        if evolution_enabled and vendor.phone:
            message = self.env['evolution.message']._send_text(
                vendor.phone, self._get_whatsapp_text(order), partner=vendor, document=order,
            )
            if message.state == 'sent':
                order.write({'state': 'sent'})
                return 'WhatsApp'
            order.message_post(body=_("WhatsApp failed (%s). Trying email.", html_escape(message.error or '')), subtype_xmlid='mail.mt_note')
        if vendor.email:
            order.with_context(mark_rfq_as_sent=True).message_post_with_source(
                RFQ_EMAIL_TEMPLATE, email_layout_xmlid=EMAIL_LAYOUT,
                subtype_xmlid='mail.mt_comment', message_type='comment',
            )
            return 'email'
        return False

    def action_approve_and_send(self):
        self.ensure_one()
        if self.state != 'to_approve':
            raise UserError(_("Only requests waiting for approval can be sent."))
        draft_orders = self.order_ids.filtered(lambda order: order.state == 'draft')
        if not draft_orders:
            raise UserError(_("There are no draft requests for quotation to send."))
        draft_orders._check_vendors_accept_direct_contact()  # a vendor may have been flagged after the draft

        channels = {}
        for order in draft_orders:
            channels[order] = self._send_order(order)
        sent_orders = self.env['purchase.order'].concat(*[order for order, channel in channels.items() if channel])
        if not sent_orders:
            raise UserError(_("No request could be sent: check the phone, email and Evolution API settings of the vendors."))

        now = fields.Datetime.now()
        self.write({
            'state': 'waiting',
            'sent_date': now,
            'deadline_date': now + timedelta(hours=self._get_wait_hours()),
        })
        summary = "<br/>".join(
            f"{html_escape(order.partner_id.display_name)}: {channel or _('NOT SENT (no phone or email)')}"
            for order, channel in channels.items()
        )
        self.message_post(body=_("Approved by %(user)s. Sent:<br/>%(summary)s<br/>Waiting until %(deadline)s.",
                                 user=self.env.user.display_name, summary=summary, deadline=self.deadline_date))
        self.activity_ids.sudo().action_feedback()
        return True

    def action_cancel(self):
        for request in self:
            if request.state in ('done', 'cancel'):
                continue
            request.order_ids.filtered(lambda order: order.state in ('draft', 'sent')).button_cancel()
            request.state = 'cancel'
            request.activity_ids.sudo().unlink()
        return True

    def action_mark_done(self):
        self.write({'state': 'done'})

    # ------------------------------------------------------------
    # WAITING PERIOD
    # ------------------------------------------------------------

    @api.model
    def _cron_check_deadlines(self):
        now = fields.Datetime.now()
        waiting_requests = self.search([('state', '=', 'waiting')])
        for request in waiting_requests:
            answered = request.order_ids.filtered('ai_quote_registered')
            if request.deadline_date > now and len(answered) < len(request.order_ids):
                continue
            request.state = 'ready'
            request.message_post(body=_(
                "The waiting period ended: %(answered)s of %(total)s quotations registered. "
                "Ask the Buyer Agent to review request %(name)s.",
                answered=len(answered), total=len(request.order_ids), name=request.name,
            ))
            request.activity_schedule(
                'mail.mail_activity_data_todo', user_id=request.user_id.id,
                summary=_("Review the quotations with the Buyer Agent"),
            )

    # ------------------------------------------------------------
    # QUOTES AND COMPARISON
    # ------------------------------------------------------------

    def _get_replies(self, order):
        """Text messages written by people outside the company, newest last."""
        internal_partners = self.env['res.users'].sudo().search([('share', '=', False)]).partner_id
        replies = order.message_ids.filtered(
            lambda message: message.author_id and message.author_id not in internal_partners
            and message.message_type in ('comment', 'email')
        ).sorted('id')
        return [{
            'from': message.author_id.display_name,
            'date': message.date,
            'text': html2plaintext(message.body or '')[:MAX_REPLY_LENGTH],
        } for message in replies[-MAX_REPLIES_PER_ORDER:]]

    def _get_last_price(self, order):
        """Unit price paid to this vendor the last time, in the order currency."""
        last_line = self.env['purchase.order.line'].search([
            ('product_id', '=', self.product_id.id),
            ('order_id.partner_id', '=', order.partner_id.id),
            ('order_id.state', 'in', ('purchase', 'done')),
            ('order_id', '!=', order.id),
        ], order='id desc', limit=1)
        return last_line.price_unit if last_line and last_line.currency_id == order.currency_id else False

    def _get_comparison(self):
        """Deterministic ranking of the registered quotes: on time first, then cheapest."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        rows = []
        for order in self.order_ids.filtered(lambda order: order.state != 'cancel'):
            total = order.currency_id._convert(
                order.amount_total, order.company_id.currency_id, order.company_id, today,
            )
            late = bool(
                order.ai_quote_registered and self.needed_date
                and today + timedelta(days=order.ai_quote_lead_days) > self.needed_date
            )
            last_price = self._get_last_price(order)
            unit_price = order.order_line[:1].price_unit
            price_change = round((unit_price - last_price) / last_price * 100, 1) if last_price else None
            flags = []
            if not order.ai_quote_registered:
                flags.append("no quote registered")
            if late:
                flags.append("delivery after the needed date")
            if price_change is not None and price_change > 10:
                flags.append(f"{price_change}% above the last price paid")
            rows.append({
                'order_id': order.id,
                'order': order.name,
                'vendor': order.partner_id.display_name,
                'quote_registered': order.ai_quote_registered,
                'unit_price': unit_price,
                'currency': order.currency_id.name,
                'total_company_currency': round(total, 2),
                'lead_days': order.ai_quote_lead_days if order.ai_quote_registered else None,
                'conditions': order.ai_quote_notes or None,
                'last_price_paid': last_price or None,
                'late': late,
                'flags': flags,
            })
        rows.sort(key=lambda row: (not row['quote_registered'], row['late'], row['total_company_currency']))
        registered = [row for row in rows if row['quote_registered']]
        return {
            'request': self.name,
            'product': self.product_id.display_name,
            'quantity': self.product_qty,
            'needed_date': self.needed_date,
            'quotes_registered': len(registered),
            'quotes_requested': len(rows),
            'warning': "Fewer than 3 quotations received: say so in the recommendation." if len(registered) < QUOTES_REQUESTED else None,
            'ranking': rows,
        }

    @api.depends('order_ids.ai_quote_registered', 'order_ids.amount_total', 'order_ids.ai_quote_lead_days')
    def _compute_comparison_html(self):
        for request in self:
            if not request.id or not request.order_ids:
                request.comparison_html = False
                continue
            body = "".join(
                f"<tr><td>{html_escape(row['vendor'])}</td><td>{row['unit_price']:.2f} {html_escape(row['currency'])}</td>"
                f"<td>{row['total_company_currency']:.2f}</td><td>{row['lead_days'] if row['lead_days'] is not None else '-'}</td>"
                f"<td>{html_escape('; '.join(row['flags']))}</td></tr>"
                for row in request._get_comparison()['ranking']
            )
            request.comparison_html = (
                "<table class='table table-sm'><thead><tr><th>Vendor</th><th>Unit price</th><th>Total</th>"
                f"<th>Days</th><th>Alerts</th></tr></thead><tbody>{body}</tbody></table>"
            )

    # ------------------------------------------------------------
    # AI TOOLS
    # ------------------------------------------------------------

    def _ai_summary(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'state': self.state,
            'product': self.product_id.display_name,
            'quantity': self.product_qty,
            'needed_date': self.needed_date,
            'reply_deadline': self.deadline_date,
            'vendors_asked': [{
                'order_id': order.id, 'order': order.name, 'vendor': order.partner_id.display_name,
                'order_state': order.state, 'quote_registered': order.ai_quote_registered,
            } for order in self.order_ids],
            'vendors_handled_manually': self.skipped_partner_ids.mapped('display_name'),
            'next_step': {
                'to_approve': "A person must open the request in Odoo and click 'Approve and send'. You cannot send.",
                'waiting': "Wait for the vendors' replies until the deadline.",
                'ready': "Read the replies, register each quote and compare.",
                'manual': "No vendor can be asked automatically. A person handles this purchase.",
            }.get(self.state),
            'url': f"{self.get_base_url()}/odoo/{self._name}/{self.id}",
        }

    @api.model
    def _ai_get_request(self, request_id):
        request = self.browse(int(request_id)).exists()
        if not request:
            raise UserError(_("The buyer request does not exist."))
        summary = request._ai_summary()
        for vendor_row, order in zip(summary['vendors_asked'], request.order_ids):
            vendor_row['replies'] = request._get_replies(order)
        return dump_json(summary)

    @api.model
    def _ai_list_requests(self, only_open=True):
        domain = [('state', 'in', OPEN_STATES)] if only_open else []
        return dump_json([{
            'id': request.id, 'name': request.name, 'product': request.product_id.display_name,
            'state': request.state, 'reply_deadline': request.deadline_date,
        } for request in self.search(domain, limit=20)])

    @api.model
    def _ai_register_quote(self, order_id, unit_price=None, lead_days=None, conditions=None):
        order = self.env['purchase.order'].browse(int(order_id)).exists()
        if not order or not order.ai_buyer_request_id:
            raise UserError(_("That order does not belong to a buyer request."))
        if order.state not in ('draft', 'sent'):
            raise UserError(_("Only draft or sent requests can receive a quote."))
        if order.ai_buyer_request_id.state not in ('waiting', 'ready'):
            raise UserError(_("The request is not waiting for quotations."))
        if order.partner_id.buy_through_intermediary:
            raise UserError(_("This vendor is handled manually."))
        if unit_price is None or not isinstance(unit_price, (int, float)) or unit_price <= 0:
            raise UserError(_("Provide the unit price the vendor quoted (greater than 0)."))
        if lead_days is not None and (not isinstance(lead_days, (int, float)) or not 0 <= lead_days <= 365):
            raise UserError(_("The lead time must be between 0 and 365 days."))
        order.order_line[:1].write({'price_unit': unit_price})
        order.write({
            'ai_quote_registered': True,
            'ai_quote_lead_days': int(lead_days or 0),
            'ai_quote_notes': conditions or False,
        })
        order.message_post(
            body=_("Quote registered by the Buyer Agent: %(price)s per unit, %(days)s day(s). Verify against the vendor's message.",
                   price=unit_price, days=int(lead_days or 0)),
            subtype_xmlid='mail.mt_note',
        )
        return dump_json(order.ai_buyer_request_id._get_comparison())

    @api.model
    def _ai_compare(self, request_id):
        request = self.browse(int(request_id)).exists()
        if not request:
            raise UserError(_("The buyer request does not exist."))
        return dump_json(request._get_comparison())

    @api.model
    def _ai_save_recommendation(self, request_id, recommendation):
        request = self.browse(int(request_id)).exists()
        if not request or request.state not in ('waiting', 'ready'):
            raise UserError(_("The request does not exist or is not waiting for quotations."))
        request.write({'recommendation': plaintext2html(recommendation), 'state': 'ready'})
        request.message_post(body=_("Recommendation of the Buyer Agent:<br/>%s", plaintext2html(recommendation)))
        return dump_json({'saved': True, 'note': "A person must review and confirm the purchase in Odoo."})
