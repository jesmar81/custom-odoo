from odoo import _, api, fields, models
from odoo.exceptions import AccessError

TEMPLATE_EDITOR_GROUP = 'mail.group_mail_template_editor'


class EvolutionTemplate(models.Model):
    _name = 'evolution.template'
    _description = "WhatsApp Message Template"
    _inherit = ['mail.render.mixin']
    _order = 'name'
    # Any user may render a template an editor wrote; only editors may write one (see create/write).
    _unrestricted_rendering = True

    name = fields.Char(required=True, translate=True)
    model_id = fields.Many2one(
        'ir.model', string="Applies to", required=True, ondelete='cascade',
        domain=[('transient', '=', False), ('field_id.name', '=', 'id')],
    )
    model = fields.Char(related='model_id.model', store=True, readonly=True)
    body = fields.Text(
        string="Message", required=True, translate=True,
        help="Use placeholders such as {{ object.partner_id.name }} or {{ object.name }}.",
    )
    report_id = fields.Many2one(
        'ir.actions.report', string="Attach PDF of the document",
        domain="[('model', '=', model), ('report_type', '=', 'qweb-pdf')]",
    )
    attachment_ids = fields.Many2many('ir.attachment', string="Fixed files")
    active = fields.Boolean(default=True)

    @api.depends('model')
    def _compute_render_model(self):
        for template in self:
            template.render_model = template.model

    def _check_editor_rights(self):
        if not (self.env.su or self.env.user.has_group(TEMPLATE_EDITOR_GROUP)):
            raise AccessError(_("Only template editors can change WhatsApp templates."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_editor_rights()
        return super().create(vals_list)

    def write(self, vals):
        self._check_editor_rights()
        return super().write(vals)

    def _render_body(self, document):
        self.ensure_one()
        return self._render_field('body', [document.id], compute_lang=True)[document.id]
