# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models

LICENSE_EXPIRY_WARNING_DAYS = 30
LOGISTICS_FIELD_GROUPS = 'arbz_delivery_route.group_logistics_user,hr.group_hr_user'


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    is_logistics_driver = fields.Boolean(string='Is Logistics Driver', groups=LOGISTICS_FIELD_GROUPS)
    logistics_license_number = fields.Char(string='Driver License ID', groups=LOGISTICS_FIELD_GROUPS)
    logistics_license_type = fields.Selection([
        ('state', 'State driver license'),
        ('federal', 'Federal driver license'),
    ], string='License Type', groups=LOGISTICS_FIELD_GROUPS,
        help='Indicates if the license is issued by the state or by the federation')
    logistics_license_subtype = fields.Char(
        string='License Subtype', groups=LOGISTICS_FIELD_GROUPS,
        help='Type of license to know which vehicles the driver can operate')
    logistics_license_valid_from = fields.Date(string='License Valid From', groups=LOGISTICS_FIELD_GROUPS)
    logistics_license_expiration = fields.Date(string='License Expiration', groups=LOGISTICS_FIELD_GROUPS)
    logistics_license_days_to_expire = fields.Integer(
        string='Days to Expire', compute='_compute_logistics_license_days_to_expire',
        groups=LOGISTICS_FIELD_GROUPS)

    # Technical partner used as the driver's live marker on the map view
    # (web_map only plots res.partner coordinates).
    logistics_tracking_partner_id = fields.Many2one(
        'res.partner', string='Live Position Marker', copy=False, readonly=True, groups=LOGISTICS_FIELD_GROUPS)
    logistics_last_position_at = fields.Datetime(
        string='Last Position At', copy=False, readonly=True, groups=LOGISTICS_FIELD_GROUPS)
    logistics_position_ids = fields.One2many(
        'logistics.position', 'employee_id', string='GPS Positions', groups=LOGISTICS_FIELD_GROUPS)

    @api.depends('logistics_license_expiration')
    def _compute_logistics_license_days_to_expire(self):
        today = fields.Date.context_today(self)
        for employee in self:
            if employee.logistics_license_expiration:
                employee.logistics_license_days_to_expire = max(
                    (employee.logistics_license_expiration - today).days, 0)
            else:
                employee.logistics_license_days_to_expire = 0

    def _update_live_position(self, position):
        self.ensure_one()
        if not self.logistics_tracking_partner_id:
            self.logistics_tracking_partner_id = self.env['res.partner'].create({
                'name': _('%s (GPS)', self.name),
                'type': 'other',
                'parent_id': self.company_id.partner_id.id,
                'company_id': self.company_id.id,
            })
        if position.recorded_at < (self.logistics_last_position_at or position.recorded_at):
            return
        self.logistics_tracking_partner_id.write({
            'partner_latitude': position.latitude,
            'partner_longitude': position.longitude,
            'date_localization': fields.Date.to_date(position.recorded_at),
        })
        self.logistics_last_position_at = position.recorded_at

    @api.model
    def _cron_notify_license_expiration(self):
        warning_date = fields.Date.context_today(self) + timedelta(days=LICENSE_EXPIRY_WARNING_DAYS)
        expiring_drivers = self.sudo().search([
            ('is_logistics_driver', '=', True),
            ('logistics_license_expiration', '!=', False),
            ('logistics_license_expiration', '<=', warning_date),
        ])
        activity_type = self.env.ref('mail.mail_activity_data_todo')
        for driver in expiring_drivers:
            responsible_user = driver.parent_id.user_id or driver.company_id.partner_id.user_id or self.env.user
            already_notified = driver.activity_ids.filtered(
                lambda activity: activity.activity_type_id == activity_type
                and activity.summary == _('Driver license expiring'))
            if already_notified:
                continue
            driver.activity_schedule(
                activity_type_id=activity_type.id,
                summary=_('Driver license expiring'),
                note=_('The driver license of %(driver)s expires on %(date)s.',
                       driver=driver.name, date=driver.logistics_license_expiration),
                user_id=responsible_user.id,
                date_deadline=driver.logistics_license_expiration,
            )
