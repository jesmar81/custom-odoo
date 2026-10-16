from odoo import SUPERUSER_ID, api

ADDENDA_NAME = 'Addenda Femsa Autopartes'
XMLID_MODULE = 'l10n_mx_femsa'
XMLID_NAME = 'addenda_femsa_autopartes'


def migrate(cr, version):
    """Re-enlaza el xmlid del módulo con la adenda ya convertida en Odoo 19.

    Al actualizar desde 17.0, l10n_mx_edi convierte la adenda a un registro
    ``l10n_mx_edi.addenda``, pero el xmlid antiguo queda colgando apuntando a
    la vista retirada. Aquí se re-apunta al registro existente para que el
    archivo de datos lo actualice en lugar de crear un duplicado.
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    addenda = env['l10n_mx_edi.addenda'].search([('name', '=', ADDENDA_NAME)], limit=1)
    xmlid = env['ir.model.data'].search([
        ('module', '=', XMLID_MODULE),
        ('name', '=', XMLID_NAME),
    ], limit=1)

    if not addenda:
        if xmlid:
            xmlid.unlink()
        return

    values = {
        'model': 'l10n_mx_edi.addenda',
        'res_id': addenda.id,
        'noupdate': False,
    }
    if xmlid:
        xmlid.write(values)
    else:
        env['ir.model.data'].create({
            'module': XMLID_MODULE,
            'name': XMLID_NAME,
            **values,
        })
