# -*- coding: utf-8 -*-
"""REST API v1 for the drivers' mobile app and the GPS tracker platform.

Authentication: ``Authorization: Bearer <api key>`` (Odoo user API keys).
Every response has the shape ``{"ok": bool, "data": ..., "error": {"code", "message"}}``.
"""
import base64
import binascii
import functools
import logging
from datetime import datetime, timedelta, timezone

import pytz
from werkzeug.exceptions import HTTPException

from odoo import fields, http
from odoo.exceptions import AccessDenied, AccessError, MissingError, UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

API_PREFIX = '/api/logistics/v1'
API_KEY_NAME = 'Logistics mobile app'
MAX_EVENTS_PER_SYNC = 200
MAX_POSITIONS_PER_BATCH = 500
MAX_PHOTOS_PER_DELIVERY = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_LENGTH = 2000
KNOTS_TO_KMH = 1.852
DRIVER_ACTIONS = ('start', 'arrive', 'deliver', 'fail', 'note')
FAILURE_REASONS = ('customer_absent', 'address_not_found', 'refused', 'closed', 'damaged', 'other')
SYNC_WINDOW_PAST_DAYS = 1
# Stops still being planned by the office are not sent to the app.
HIDDEN_FROM_DRIVER_STATES = ('draft', 'requested')
SYNC_WINDOW_FUTURE_DAYS = 7


class ApiError(Exception):
    def __init__(self, http_status, error_code, message):
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code
        self.message = message


# ----------------------------------------------------------------------
# Responses and error handling
# ----------------------------------------------------------------------

def _json_response(payload, http_status=200):
    return request.make_json_response({'ok': True, 'data': payload}, status=http_status)


def _error_response(http_status, error_code, message):
    return request.make_json_response(
        {'ok': False, 'error': {'code': error_code, 'message': message}}, status=http_status)


def api_endpoint(endpoint_method):
    """Translate exceptions into JSON errors without leaking tracebacks, and roll back on failure."""
    @functools.wraps(endpoint_method)
    def wrapper(*args, **kwargs):
        try:
            return endpoint_method(*args, **kwargs)
        except ApiError as err:
            request.env.cr.rollback()
            return _error_response(err.http_status, err.error_code, err.message)
        except (AccessError, AccessDenied) as err:
            request.env.cr.rollback()
            return _error_response(403, 'forbidden', str(err) or 'Access denied.')
        except MissingError:
            request.env.cr.rollback()
            return _error_response(404, 'not_found', 'Record not found.')
        except (UserError, ValidationError) as err:
            request.env.cr.rollback()
            return _error_response(409, 'business_rule', str(err))
        except HTTPException:
            raise
        except Exception:
            request.env.cr.rollback()
            _logger.exception('Logistics API error on %s', request.httprequest.path)
            return _error_response(500, 'server_error', 'Unexpected server error.')
    return wrapper


# ----------------------------------------------------------------------
# Payload validation
# ----------------------------------------------------------------------

def _parse_float(raw_value, field_name, minimum=None, maximum=None, required=False):
    if raw_value in (None, ''):
        if required:
            raise ApiError(422, 'invalid_payload', f'{field_name} is required.')
        return None
    try:
        parsed_value = float(raw_value)
    except (TypeError, ValueError):
        raise ApiError(422, 'invalid_payload', f'{field_name} must be a number.') from None
    if (minimum is not None and parsed_value < minimum) or (maximum is not None and parsed_value > maximum):
        raise ApiError(422, 'invalid_payload', f'{field_name} is out of range.')
    return parsed_value


def _parse_coordinates(payload, required=False):
    latitude = _parse_float(payload.get('latitude'), 'latitude', -90, 90, required)
    longitude = _parse_float(payload.get('longitude'), 'longitude', -180, 180, required)
    if (latitude is None) != (longitude is None):
        raise ApiError(422, 'invalid_payload', 'latitude and longitude go together.')
    return latitude, longitude


def _parse_datetime(raw_value, field_name):
    """ISO 8601 string -> naive UTC datetime (Odoo storage format)."""
    if not raw_value:
        return None
    if not isinstance(raw_value, str):
        raise ApiError(422, 'invalid_payload', f'{field_name} must be an ISO 8601 string.')
    try:
        parsed_datetime = datetime.fromisoformat(raw_value.replace('Z', '+00:00'))
    except ValueError:
        raise ApiError(422, 'invalid_payload', f'{field_name} must be an ISO 8601 string.') from None
    if parsed_datetime.tzinfo:
        parsed_datetime = parsed_datetime.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed_datetime > fields.Datetime.now() + timedelta(minutes=10):
        raise ApiError(422, 'invalid_payload', f'{field_name} is in the future.')
    return parsed_datetime


def _parse_date(raw_value):
    if not raw_value:
        return fields.Date.context_today(request.env.user)
    try:
        return fields.Date.to_date(raw_value)
    except ValueError:
        raise ApiError(422, 'invalid_payload', 'date must be YYYY-MM-DD.') from None


def _parse_text(raw_value, field_name):
    if raw_value in (None, ''):
        return None
    if not isinstance(raw_value, str):
        raise ApiError(422, 'invalid_payload', f'{field_name} must be a string.')
    if len(raw_value) > MAX_TEXT_LENGTH:
        raise ApiError(422, 'invalid_payload', f'{field_name} is too long.')
    return raw_value.strip()


def _parse_image(raw_value, field_name):
    """Validate a base64 image and return it as base64 bytes (Odoo binary format)."""
    if not raw_value:
        return None
    if not isinstance(raw_value, str):
        raise ApiError(422, 'invalid_payload', f'{field_name} must be base64.')
    base64_content = raw_value.split(',', 1)[1] if raw_value.startswith('data:') else raw_value
    try:
        image_bytes = base64.b64decode(base64_content, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(422, 'invalid_payload', f'{field_name} is not valid base64.') from None
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ApiError(413, 'payload_too_large', f'{field_name} exceeds {MAX_IMAGE_BYTES // 1024 // 1024} MB.')
    if not (image_bytes.startswith(b'\xff\xd8') or image_bytes.startswith(b'\x89PNG')):
        raise ApiError(422, 'invalid_payload', f'{field_name} must be a JPEG or PNG image.')
    return base64.b64encode(image_bytes)


def _parse_driver_event(payload, action=None):
    if not isinstance(payload, dict):
        raise ApiError(422, 'invalid_payload', 'Each event must be an object.')
    action = action or payload.get('action')
    if action not in DRIVER_ACTIONS:
        raise ApiError(422, 'invalid_payload', f'action must be one of {", ".join(DRIVER_ACTIONS)}.')
    latitude, longitude = _parse_coordinates(payload)
    failure_reason = payload.get('failure_reason')
    if failure_reason and failure_reason not in FAILURE_REASONS:
        raise ApiError(422, 'invalid_payload', f'failure_reason must be one of {", ".join(FAILURE_REASONS)}.')
    photos = payload.get('photos') or []
    if not isinstance(photos, list) or len(photos) > MAX_PHOTOS_PER_DELIVERY:
        raise ApiError(422, 'invalid_payload', f'photos must be a list of up to {MAX_PHOTOS_PER_DELIVERY} images.')
    sync_uuid = _parse_text(payload.get('sync_uuid'), 'sync_uuid')
    if sync_uuid and len(sync_uuid) > 64:
        raise ApiError(422, 'invalid_payload', 'sync_uuid is too long.')
    return {
        'action': action,
        'sync_uuid': sync_uuid,
        'event_datetime': _parse_datetime(payload.get('event_datetime'), 'event_datetime'),
        'latitude': latitude,
        'longitude': longitude,
        'note': _parse_text(payload.get('note'), 'note'),
        'failure_reason': failure_reason,
        'received_by_name': _parse_text(payload.get('received_by_name'), 'received_by_name'),
        'signature': _parse_image(payload.get('signature'), 'signature'),
        'photos': [_parse_image(photo, f'photos[{photo_index}]') for photo_index, photo in enumerate(photos)],
    }


def _parse_position(payload):
    if not isinstance(payload, dict):
        raise ApiError(422, 'invalid_payload', 'Each position must be an object.')
    latitude, longitude = _parse_coordinates(payload, required=True)
    return {
        'latitude': latitude,
        'longitude': longitude,
        'accuracy_m': _parse_float(payload.get('accuracy_m'), 'accuracy_m', 0) or 0.0,
        'speed_kmh': _parse_float(payload.get('speed_kmh'), 'speed_kmh', 0, 400) or 0.0,
        'heading_deg': _parse_float(payload.get('heading_deg'), 'heading_deg', 0, 360) or 0.0,
        'ignition': bool(payload.get('ignition')),
        'recorded_at': _parse_datetime(payload.get('recorded_at'), 'recorded_at') or fields.Datetime.now(),
    }


def _parse_batch(payload, key, max_items):
    batch_items = (payload or {}).get(key)
    if not isinstance(batch_items, list) or not batch_items:
        raise ApiError(422, 'invalid_payload', f'{key} must be a non-empty list.')
    if len(batch_items) > max_items:
        raise ApiError(413, 'payload_too_large', f'Send at most {max_items} {key} per request.')
    return batch_items


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------

def _serialize_partner(partner):
    if not partner:
        return None
    return {
        'id': partner.id,
        'name': partner.name,
        'street_name': partner.street_name or None,
        'street_number': partner.street_number or None,
        'street_number2': partner.street_number2 or None,
        'colony': partner.l10n_mx_edi_colony or None,
        'city': partner.city or None,
        'state': partner.state_id.name or None,
        'zip': partner.zip or None,
        'country': partner.country_id.name or None,
        'vat': partner.vat or None,
        'phone': partner.phone or None,
        'email': partner.email or None,
        'latitude': partner.partner_latitude or None,
        'longitude': partner.partner_longitude or None,
    }


def _serialize_datetime(datetime_value):
    return datetime_value and datetime_value.replace(tzinfo=timezone.utc).isoformat()


def _serialize_route(route):
    # The caller already fetched the stop under the driver's record rules; sudo
    # only lets the app see related documents (orders, vehicle) drivers cannot open.
    route = route.sudo()
    return {
        'id': route.id,
        'name': route.name,
        'sequence': route.sequence,
        'date': fields.Date.to_string(route.date),
        'state': route.state,
        'start_time': route.start_time,
        'end_time': route.end_time,
        'lunch_start_time': route.lunch_start_time,
        'lunch_end_time': route.lunch_end_time,
        'planned_arrival': _serialize_datetime(route.planned_arrival),
        'started_at': _serialize_datetime(route.started_at),
        'arrived_at': _serialize_datetime(route.arrived_at),
        'delivered_at': _serialize_datetime(route.delivered_at),
        'customer': _serialize_partner(route.customer_id),
        'branch': _serialize_partner(route.branch_id),
        'attendant': _serialize_partner(route.attendant_id),
        'courier_service': route.courier_service_id.name or None,
        'payment_method': route.payment_method,
        'payment_type': route.payment_type,
        'service_type': route.service_type,
        'sale_orders': [{'id': sale_order.id, 'name': sale_order.name,
                         'date_order': fields.Date.to_string(sale_order.date_order)}
                        for sale_order in route.sale_order_ids],
        'purchase_orders': [{'id': purchase_order.id, 'name': purchase_order.name,
                             'date_order': fields.Date.to_string(purchase_order.date_order)}
                            for purchase_order in route.purchase_order_ids],
        'vehicle': route.vehicle_id and {
            'id': route.vehicle_id.id,
            'name': route.vehicle_id.name,
            'license_plate': route.vehicle_id.license_plate,
        } or None,
        'observation': route.observation or None,
        'navigation_url': route.navigation_url or None,
        'failure_reason': route.failure_reason or None,
        'received_by_name': route.received_by_name or None,
        'has_signature': bool(route.delivery_signature),
        'photo_count': len(route.delivery_photo_ids),
        'write_date': _serialize_datetime(route.write_date),
    }


def _serialize_event_result(sync_uuid, status, route=None, error_code=None, message=None):
    return {
        'sync_uuid': sync_uuid,
        'status': status,
        'route_id': route.id if route else None,
        'route_state': route.state if route else None,
        'error': error_code and {'code': error_code, 'message': message},
    }


# ----------------------------------------------------------------------
# Controller
# ----------------------------------------------------------------------

class LogisticsApiV1(http.Controller):

    @staticmethod
    def _get_current_driver():
        user = request.env.user
        if not (user.has_group('arbz_delivery_route.group_logistics_driver')
                or user.has_group('arbz_delivery_route.group_logistics_supervisor')):
            raise ApiError(403, 'not_a_driver', 'This user is not a logistics driver.')
        employee = user.employee_id
        if not employee:
            raise ApiError(403, 'not_a_driver', 'This user has no employee record.')
        return employee

    @staticmethod
    def _get_route(route_id):
        route = request.env['logistics.route'].search([
            ('id', '=', route_id),
            ('driver_user_id', '=', request.env.uid),
            ('state', 'not in', HIDDEN_FROM_DRIVER_STATES),
        ], limit=1)
        if not route:
            raise ApiError(404, 'not_found', 'Stop not found.')
        return route

    @staticmethod
    def _driver_routes_domain(employee, date_from, date_to):
        return [
            ('driver_employee_id', '=', employee.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('state', 'not in', HIDDEN_FROM_DRIVER_STATES),
        ]

    # -------------------------------- auth --------------------------------

    @http.route(f'{API_PREFIX}/auth/login', type='json2', auth='none', methods=['POST'], readonly=False,
                save_session=False)
    @api_endpoint
    def login(self, login=None, password=None, device_name=None, **kwargs):
        if not request.db:
            raise ApiError(400, 'no_database', 'No database selected; configure dbfilter on the server.')
        if not isinstance(login, str) or not isinstance(password, str) or not login or not password:
            raise ApiError(422, 'invalid_payload', 'login and password are required.')
        credential = {'type': 'password', 'login': login, 'password': password}
        try:
            # _login already throttles repeated failures (res.users._assert_can_auth).
            auth_info = request.env['res.users'].authenticate(credential, {'interactive': False})
        except AccessDenied:
            raise ApiError(401, 'invalid_credentials', 'Invalid login or password.') from None
        user = request.env['res.users'].browse(auth_info['uid'])
        request.update_env(user=user)
        employee = self._get_current_driver()

        api_key_days = int(request.env['ir.config_parameter'].sudo().get_param(
            'arbz_delivery_route.api_key_days', 30))
        expiration_datetime = fields.Datetime.now() + timedelta(days=api_key_days)
        key_name = f'{API_KEY_NAME}: {_parse_text(device_name, "device_name") or "device"}'[:100]
        # sudo: the key duration is set by the logistics settings, not by the
        # per-group API key limits meant for interactive users.
        api_key = request.env['res.users.apikeys'].with_user(user).sudo()._generate(
            None, key_name, expiration_datetime)
        return _json_response({
            'token': api_key,
            'token_type': 'Bearer',
            'expires_at': _serialize_datetime(expiration_datetime),
            'driver': self._serialize_driver(employee),
        })

    @http.route(f'{API_PREFIX}/auth/logout', type='json2', auth='bearer', methods=['POST'], save_session=False)
    @api_endpoint
    def logout(self, **kwargs):
        request.env['res.users.apikeys'].sudo().search([
            ('user_id', '=', request.env.uid),
            ('name', '=like', f'{API_KEY_NAME}:%'),
        ])._remove()
        return _json_response({'logged_out': True})

    # -------------------------------- driver --------------------------------

    @staticmethod
    def _serialize_driver(employee):
        employee_sudo = employee.sudo()
        vehicle = request.env['fleet.vehicle'].sudo().search([('driver_employee_id', '=', employee.id)], limit=1)
        return {
            'employee_id': employee.id,
            'name': employee.name,
            'user_id': employee.user_id.id,
            'company': employee.company_id.name,
            'tz': employee.user_id.tz or 'UTC',
            'license': {
                'number': employee_sudo.logistics_license_number or None,
                'type': employee_sudo.logistics_license_type or None,
                'expiration': fields.Date.to_string(employee_sudo.logistics_license_expiration) or None,
                'days_to_expire': employee_sudo.logistics_license_days_to_expire,
            },
            'vehicle': vehicle and {
                'id': vehicle.id,
                'name': vehicle.name,
                'license_plate': vehicle.license_plate,
                'has_gps_tracker': bool(vehicle.gps_device_identifier),
            } or None,
        }

    @http.route(f'{API_PREFIX}/me', type='json2', auth='bearer', methods=['GET'], readonly=True, save_session=False)
    @api_endpoint
    def me(self, **kwargs):
        return _json_response(self._serialize_driver(self._get_current_driver()))

    @http.route(f'{API_PREFIX}/routes', type='json2', auth='bearer', methods=['GET'], readonly=True,
                save_session=False)
    @api_endpoint
    def list_routes(self, **kwargs):
        employee = self._get_current_driver()
        route_date = _parse_date(request.httprequest.args.get('date'))
        routes = request.env['logistics.route'].search(
            self._driver_routes_domain(employee, route_date, route_date), order='sequence, planned_arrival, id')
        return _json_response({
            'date': fields.Date.to_string(route_date),
            'routes': [_serialize_route(route) for route in routes],
        })

    @http.route(f'{API_PREFIX}/routes/<int:route_id>', type='json2', auth='bearer', methods=['GET'],
                readonly=True, save_session=False)
    @api_endpoint
    def get_route(self, route_id, **kwargs):
        self._get_current_driver()
        return _json_response(_serialize_route(self._get_route(route_id)))

    @http.route(f'{API_PREFIX}/routes/<int:route_id>/actions/<string:action>', type='json2', auth='bearer',
                methods=['POST'], save_session=False)
    @api_endpoint
    def route_action(self, route_id, action, **payload):
        self._get_current_driver()
        route = self._get_route(route_id)
        driver_event = _parse_driver_event(payload, action=action)
        route_event = route._apply_driver_event(driver_event)
        return _json_response({'event_id': route_event.id, 'route': _serialize_route(route)})

    @http.route(f'{API_PREFIX}/positions', type='json2', auth='bearer', methods=['POST'], save_session=False)
    @api_endpoint
    def push_positions(self, **payload):
        employee = self._get_current_driver()
        position_values_list = [_parse_position(position_payload) for position_payload in
                                _parse_batch(payload, 'positions', MAX_POSITIONS_PER_BATCH)]
        positions = request.env['logistics.position']._record_app_positions(employee, position_values_list)
        return _json_response({'accepted': len(positions)}, http_status=201)

    # -------------------------------- offline sync --------------------------------

    @http.route(f'{API_PREFIX}/sync', type='json2', auth='bearer', methods=['POST'], save_session=False)
    @api_endpoint
    def push_sync(self, **payload):
        """Apply queued offline events in order; each one succeeds or fails on its own."""
        self._get_current_driver()
        sync_results = []
        for event_payload in _parse_batch(payload, 'events', MAX_EVENTS_PER_SYNC):
            sync_uuid = event_payload.get('sync_uuid') if isinstance(event_payload, dict) else None
            route = None
            try:
                with request.env.cr.savepoint():
                    if not sync_uuid:
                        raise ApiError(422, 'invalid_payload', 'sync_uuid is required for offline events.')
                    route_id = event_payload.get('route_id')
                    if not isinstance(route_id, int):
                        raise ApiError(422, 'invalid_payload', 'route_id must be an integer.')
                    route = self._get_route(route_id)
                    already_synced = request.env['logistics.route.event'].sudo().search_count(
                        [('sync_uuid', '=', sync_uuid)], limit=1)
                    route._apply_driver_event(_parse_driver_event(event_payload))
                    sync_results.append(_serialize_event_result(
                        sync_uuid, 'duplicate' if already_synced else 'applied', route))
            except ApiError as err:
                sync_results.append(_serialize_event_result(sync_uuid, 'rejected', route, err.error_code, err.message))
            except (UserError, ValidationError) as err:
                # Typical offline conflict: the office changed the stop meanwhile.
                if route:
                    route.invalidate_recordset()
                sync_results.append(_serialize_event_result(sync_uuid, 'conflict', route, 'business_rule', str(err)))
            except AccessError as err:
                sync_results.append(_serialize_event_result(sync_uuid, 'rejected', route, 'forbidden', str(err)))
        return _json_response({'results': sync_results, 'server_time': _serialize_datetime(fields.Datetime.now())})

    @http.route(f'{API_PREFIX}/sync', type='json2', auth='bearer', methods=['GET'], readonly=True,
                save_session=False)
    @api_endpoint
    def pull_sync(self, **kwargs):
        """Stops changed since ``since`` in the offline window (yesterday .. next week)."""
        employee = self._get_current_driver()
        since_datetime = _parse_datetime(request.httprequest.args.get('since'), 'since')
        user_timezone = pytz.timezone(request.env.user.tz or 'UTC')
        today = datetime.now(user_timezone).date()
        route_domain = self._driver_routes_domain(
            employee, today - timedelta(days=SYNC_WINDOW_PAST_DAYS), today + timedelta(days=SYNC_WINDOW_FUTURE_DAYS))
        if since_datetime:
            route_domain.append(('write_date', '>', since_datetime))
        routes = request.env['logistics.route'].search(route_domain, order='date, sequence, id')
        # Stops taken away from this driver must disappear from the device cache.
        removed_route_ids = []
        if since_datetime:
            removed_route_ids = request.env['logistics.route.event'].sudo().search([
                ('employee_id', '=', employee.id),
                ('route_id.driver_employee_id', '!=', employee.id),
                ('route_id.write_date', '>', since_datetime),
            ]).route_id.ids
        return _json_response({
            'server_time': _serialize_datetime(fields.Datetime.now()),
            'window': {
                'from': fields.Date.to_string(today - timedelta(days=SYNC_WINDOW_PAST_DAYS)),
                'to': fields.Date.to_string(today + timedelta(days=SYNC_WINDOW_FUTURE_DAYS)),
            },
            'routes': [_serialize_route(route) for route in routes],
            'removed_route_ids': removed_route_ids,
        })

    # -------------------------------- GPS trackers --------------------------------

    @staticmethod
    def _check_gps_integration_user():
        if not request.env.user.has_group('arbz_delivery_route.group_logistics_gps_integration'):
            raise ApiError(403, 'forbidden', 'This user cannot push GPS tracker positions.')

    @http.route(f'{API_PREFIX}/devices/positions', type='json2', auth='bearer', methods=['POST'],
                save_session=False)
    @api_endpoint
    def push_device_positions(self, **payload):
        """Generic batch: ``{"positions": [{"device_id": "<IMEI>", "latitude": .., "longitude": .., ...}]}``."""
        self._check_gps_integration_user()
        position_values_list = []
        for position_payload in _parse_batch(payload, 'positions', MAX_POSITIONS_PER_BATCH):
            device_identifier = _parse_text(
                position_payload.get('device_id') if isinstance(position_payload, dict) else None, 'device_id')
            if not device_identifier:
                raise ApiError(422, 'invalid_payload', 'device_id is required.')
            position_values_list.append({**_parse_position(position_payload), 'device_identifier': device_identifier})
        positions, unknown_device_identifiers = request.env['logistics.position']._record_device_positions(
            position_values_list)
        return _json_response({'accepted': len(positions), 'unknown_devices': unknown_device_identifiers},
                              http_status=201)

    @http.route(f'{API_PREFIX}/devices/traccar', type='json2', auth='bearer', methods=['POST'],
                save_session=False)
    @api_endpoint
    def push_traccar_position(self, **payload):
        """Traccar position forwarding (``forward.type = json``): ``{"position": {...}, "device": {...}}``."""
        self._check_gps_integration_user()
        traccar_position = payload.get('position')
        traccar_device = payload.get('device')
        if not isinstance(traccar_position, dict) or not isinstance(traccar_device, dict):
            raise ApiError(422, 'invalid_payload', 'position and device objects are required.')
        device_identifier = _parse_text(traccar_device.get('uniqueId'), 'device.uniqueId')
        if not device_identifier:
            raise ApiError(422, 'invalid_payload', 'device.uniqueId is required.')
        speed_knots = _parse_float(traccar_position.get('speed'), 'speed', 0) or 0.0
        traccar_attributes = traccar_position.get('attributes') or {}
        position_values = _parse_position({
            'latitude': traccar_position.get('latitude'),
            'longitude': traccar_position.get('longitude'),
            'accuracy_m': traccar_position.get('accuracy'),
            'speed_kmh': round(speed_knots * KNOTS_TO_KMH, 2),
            'heading_deg': traccar_position.get('course'),
            'ignition': traccar_attributes.get('ignition') if isinstance(traccar_attributes, dict) else False,
            'recorded_at': traccar_position.get('fixTime') or traccar_position.get('deviceTime'),
        })
        positions, unknown_device_identifiers = request.env['logistics.position']._record_device_positions(
            [{**position_values, 'device_identifier': device_identifier}])
        return _json_response({'accepted': len(positions), 'unknown_devices': unknown_device_identifiers},
                              http_status=201)
