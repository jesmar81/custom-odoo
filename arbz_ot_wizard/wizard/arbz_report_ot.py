from odoo import _, fields, models

class ArbzReportOt(models.TransientModel):
    _name = 'arbz.ot.wizard'
    _description = "Wizard Report OT"

    start_date = fields.Date(string="Fecha Inicial")
    end_date = fields.Date(string="Fecha Final")
    report_lines = fields.One2many('wizard.report.line', 'report_id', string="Reporte")

    def generate_report(self):
        self.ensure_one()
        # Llamar a la función de PostgreSQL para obtener los resultados
        query = """SELECT * FROM public.total_subtotal_orden_17(%s, %s)"""
        self.env.cr.execute(query, (self.start_date, self.end_date))
        results = self.env.cr.fetchall()

        # Crear los objetos de líneas de reporte
        report_line_obj = self.env['wizard.report.line']
        report_lines = [(0, 0, {
            'report_id': self.id,
            'orden': result[0],
            'total': result[1]
        }) for result in results]
        self.report_lines = report_lines

        # Abrir la vista de resultados de reporte en una página separada
        view = self.env.ref('arbz_ot_wizard.wizard_report_line_view')  # Reemplaza 'nombre_del_modulo' con el nombre real de tu módulo
        return {
            'name': _('Results of the amount of work orders.'),
            'view_mode': 'list',
            'res_model': 'wizard.report.line',
            'view_id': view.id if view else False,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'domain': [('id', 'in', self.report_lines.ids)],
        }

class WizardReportLine(models.TransientModel):
    _name = 'wizard.report.line'
    _description = "Wizard Report OT Line"

    report_id = fields.Many2one('arbz.ot.wizard', string="Reporte")
    orden = fields.Char(string="Orden de Trabajo")
    total = fields.Float(string="Total", digits='Product Price')
