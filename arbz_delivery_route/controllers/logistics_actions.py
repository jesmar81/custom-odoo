from odoo import http
from odoo.http import request


class LogisticsActions(http.Controller):

    @http.route('/logistics/change_state', type='json', auth='user')
    def change_state(self, **kwargs):
        record_id = kwargs.get('record_id')
        new_state = kwargs.get('new_state')

        if not record_id or not new_state:
            return {'error': 'Record ID or new state missing.'}

        logistics = request.env['logistics.route'].browse(int(record_id))
        if not logistics:
            return {'error': 'Logistics route not found.'}

        allowed_states = ['draft', 'requested', 'assigned', 'on_route', 'delivered', 'postponed', 'cancelled']
        if new_state not in allowed_states:
            return {'error': 'Invalid state change.'}

        if new_state == 'draft' and logistics.state != 'cancelled':
            return {'error': 'You cannot change a logistics route to draft state.'}

        if new_state == 'requested' and logistics.state != 'draft':
            return {'error': 'You can only change a logistics route to requested state from draft state.'}

        if new_state == 'assigned' and logistics.state != 'requested':
            return {'error': 'You can only change a logistics route to assigned state from requested state.'}

        if new_state == 'on_route' and logistics.state != 'assigned':
            return {'error': 'You can only change a logistics route to on route state from assigned state.'}

        if new_state == 'delivered' and logistics.state != 'on_route':
            return {'error': 'You can only change a logistics route to delivered state from on route state.'}

        if new_state == 'postponed' and logistics.state in ['delivered', 'postponed']:
            return {'error': 'You can only change a logistics route to postponed state from delivered or postponed state.'}

        if new_state == 'cancelled' and logistics.state != 'draft':
            return {'error': 'You can only change a logistics route to cancelled state from draft state.'}

        logistics.state = new_state

        return {'success': True}
