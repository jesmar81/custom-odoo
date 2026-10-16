# -*- coding: utf-8 -*-

from odoo import api, models

SOLUCAM_LEGAL_NAME = 'SOLUCAM SA'


class L10nMxEdiDocument(models.Model):
    _inherit = 'l10n_mx_edi.document'

    @api.model
    def _cfdi_sanitize_to_legal_name(self, name):
        """Conserva el nombre fiscal de SOLUCAM SA sin limpieza en el CFDI.

        La localización mexicana elimina sufijos como 'SA', 'S.A. de C.V.',
        'S. de R.L.', etc. del nombre. El nombre fiscal oficial de este
        cliente incluye el sufijo, por lo que se devuelve tal cual.
        """
        if name == SOLUCAM_LEGAL_NAME:
            return name
        return super()._cfdi_sanitize_to_legal_name(name)
