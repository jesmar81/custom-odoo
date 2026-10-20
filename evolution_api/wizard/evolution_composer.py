from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EvolutionComposer(models.TransientModel):
    _name = 'evolution.composer'
    _description = "Send a WhatsApp message"

    res_model = fields.Char()
    res_id = fields.Integer()
    account_id = fields.Many2one(
        'evolution.account', string="Send from", required=True,
        domain="[('id', 'in', allowed_account_ids)]",
    )
    allowed_account_ids = fields.Many2many('evolution.account', compute='_compute_allowed_account_ids')
    partner_id = fields.Many2one('res.partner', string="Contact")
    phone = fields.Char(string="Number", required=True)
    template_id = fields.Many2one('evolution.template', string="Template", domain="[('model', '=', res_model)]")
    body = fields.Text(string="Message")
    report_id = fields.Many2one(
        'ir.actions.report', string="PDF of the document",
        domain="[('model', '=', res_model), ('report_type', '=', 'qweb-pdf')]",
    )
    attach_document_pdf = fields.Boolean(string="Attach the PDF")
    attachment_ids = fields.Many2many('ir.attachment', string="Other files")

    @api.depends_context('uid')
    def _compute_allowed_account_ids(self):
        accounts = self.env['evolution.account']._get_usable_accounts()
        for composer in self:
            composer.allowed_account_ids = accounts

    def _get_document(self):
        self.ensure_one()
        if self.res_model and self.res_id:
            return self.env[self.res_model].browse(self.res_id).exists()
        return None

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id and not self.phone:
            self.phone = self.partner_id.phone

    @api.onchange('template_id')
    def _onchange_template_id(self):
        document = self._get_document()
        if self.template_id and document:
            self.body = self.template_id._render_body(document)
            if self.template_id.report_id:
                self.report_id = self.template_id.report_id
                self.attach_document_pdf = True

    def _collect_attachments(self, document):
        """Files to send, as new attachments that the message will own."""
        self.ensure_one()
        attachments = self.attachment_ids
        if self.template_id.attachment_ids:
            attachments |= self.env['ir.attachment'].browse([
                attachment.copy({'res_model': 'evolution.composer', 'res_id': self.id}).id
                for attachment in self.template_id.attachment_ids
            ])
        if self.attach_document_pdf and document:
            if not self.report_id:
                raise UserError(_("Choose the report to attach."))
            pdf_content, __ = self.env['ir.actions.report']._render_qweb_pdf(self.report_id, [document.id])
            attachments |= self.env['ir.attachment'].create({
                'name': f"{document.display_name.replace('/', '-')}.pdf",
                'raw': pdf_content, 'mimetype': 'application/pdf',
                'res_model': 'evolution.composer', 'res_id': self.id,
            })
        return attachments

    def action_send(self):
        self.ensure_one()
        if not (self.body or self.attach_document_pdf or self.attachment_ids or self.template_id.attachment_ids):
            raise UserError(_("Write a message or add a file."))
        document = self._get_document()
        messages = self.env['evolution.message']._queue_message(
            self.account_id, self.phone, self.body, partner=self.partner_id or None,
            document=document, attachments=self._collect_attachments(document), author=self.env.user,
        )
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("WhatsApp"), 'type': 'success', 'sticky': False,
                'message': _("%(count)s message(s) queued. They go out spaced to protect the number.", count=len(messages)),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
