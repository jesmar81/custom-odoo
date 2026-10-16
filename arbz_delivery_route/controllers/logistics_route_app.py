from odoo import http
from odoo.http import request
from datetime import datetime


class RouteDelivery(http.Controller):

    @staticmethod
    def get_info_dict(record, prefix):
        return {
            f'{prefix}_id': record.id,
            f'{prefix}_name': record.name,
            f'{prefix}_street_name': record.street_name,
            f'{prefix}_street_number': record.street_number,
            f'{prefix}_street_number2': record.street_number2,
            f'{prefix}_colony': record.l10n_mx_edi_colony,
            f'{prefix}_city': record.city,
            f'{prefix}_state': record.state_id.name,
            f'{prefix}_zip': record.zip,
            f'{prefix}_country': record.country_id.name,
            f'{prefix}_vat': record.vat,
            f'{prefix}_curp': record.l10n_mx_edi_curp,
            f'{prefix}_phone': record.phone,
            f'{prefix}_mobile': record.mobile,
            f'{prefix}_email': record.email,
        }

    @http.route('/logistics', type='jsonrpc', auth='user')
    def mi_ruta(self, driver_id=None, date=None, **kw):
        registros = self._get_registros(driver_id, date)

        route_data = []
        for rec in registros:
            customer_info = self.get_info_dict(rec.customer_id, 'customer')
            branch_info = self.get_info_dict(rec.branch_id, 'branch')
            attendant_info = self.get_info_dict(rec.attendant, 'attendant')

            sale_orders = [{'name': order.name, 'date_order': order.date_order.strftime('%Y-%m-%d')} for order in rec.sale_order_id]
            purchase_orders = [{'name': porder.name, 'date_order': porder.date_order.strftime('%Y-%m-%d')} for porder in rec.purchase_order_id]

            vals = {
                'name': rec.name,
                'date': rec.date.strftime('%Y-%m-%d'),
                'start_time': rec.start_time,
                'end_time': rec.end_time,
                'lunch_start_time': rec.lunch_start_time,
                'lunch_end_time': rec.lunch_end_time,
                'customer_info': customer_info,
                'branch_info': branch_info,
                'attendant_info': attendant_info,
                'courier_service': rec.courier_service_id.name,
                'payment_method': rec.payment_method,
                'payment_type': rec.payment_type,
                'service_type': rec.service_type,
                'sale_order': sale_orders,
                'purchase_order': purchase_orders,
                'driver_id': rec.driver_id.name,
                'vehicle_id': rec.vehicle_id.name,
                'observation': rec.observation,
                'state': rec.state,
            }
            route_data.append(vals)
        data = {'status': '200', 'response': route_data, 'message': 'Success'}
        return data

    def _get_registros(self, driver_id, date):
        domain = []
        if driver_id:
            domain.append(('driver_id', '=', int(driver_id)))
        if date:
            date = datetime.strptime(date, "%Y-%m-%d").date()
            domain.append(('date', '=', date))

        return request.env['logistics.route'].search(domain)

    @http.route('/change_route_state', type='jsonrpc', auth='user', methods=['POST'])
    def change_route_state(self, route_name=None, new_state=None, **kw):
        if route_name and new_state:
            domain = [('name', '=', route_name)]
            route = request.env['logistics.route'].search(domain, limit=1)
            if route:
                route.state = new_state
                return {'status': '200', 'message': 'Success'}
