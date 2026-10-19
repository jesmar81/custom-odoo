# -*- coding: utf-8 -*-
"""Route optimization: assign the stops of a day to drivers and order them.

Providers:
* ``vroom``: VROOM API, either OpenRouteService (cloud, API key) or a self-hosted
  VROOM + OSRM server. Open source.
* ``google``: Google Route Optimization API (service account).
"""
import base64
import json
import logging
from datetime import datetime, timedelta, timezone

import pytz
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 60
SECONDS_PER_HOUR = 3600
DEFAULT_VROOM_URL = 'https://api.openrouteservice.org/optimization'
GOOGLE_OPTIMIZE_URL = 'https://routeoptimization.googleapis.com/v1/projects/{project}:optimizeTours'
GOOGLE_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'
GOOGLE_DEFAULT_TOKEN_URI = 'https://oauth2.googleapis.com/token'
GOOGLE_COST_PER_KILOMETER = 1.0
GOOGLE_COST_PER_TRAVELED_HOUR = 30.0
CONFIG_PREFIX = 'arbz_delivery_route.'


def _base64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b'=').decode()


class LogisticsRouteOptimizer(models.AbstractModel):
    _name = 'logistics.route.optimizer'
    _description = 'Logistics Route Optimizer'

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @api.model
    def _get_setting(self, key, default=None):
        return self.env['ir.config_parameter'].sudo().get_param(CONFIG_PREFIX + key, default)

    @api.model
    def _get_depot_partner(self):
        depot_partner_id = int(self._get_setting('depot_partner_id', 0) or 0)
        depot_partner = self.env['res.partner'].sudo().browse(depot_partner_id).exists()
        return depot_partner or self.env.company.partner_id.sudo()

    # ------------------------------------------------------------------
    # Problem building (provider independent)
    # ------------------------------------------------------------------

    @api.model
    def _ensure_coordinates(self, partners):
        missing_partners = partners.filtered(
            lambda partner: not partner.partner_latitude and not partner.partner_longitude)
        if missing_partners:
            missing_partners.sudo().geo_localize()
        return partners.filtered(lambda partner: partner.partner_latitude or partner.partner_longitude)

    @staticmethod
    def _hours_to_seconds(hours):
        return int(round(hours * SECONDS_PER_HOUR))

    @api.model
    def _stop_time_windows(self, route, shift_start_seconds, shift_end_seconds):
        """Customer opening hours of the stop, minus its lunch closure, inside the driver shift."""
        open_seconds = max(self._hours_to_seconds(route.start_time), shift_start_seconds)
        close_seconds = min(self._hours_to_seconds(route.end_time), shift_end_seconds)
        windows = [(open_seconds, close_seconds)]
        if route.lunch_start_time and route.lunch_end_time:
            lunch_start_seconds = self._hours_to_seconds(route.lunch_start_time)
            lunch_end_seconds = self._hours_to_seconds(route.lunch_end_time)
            windows = [
                (open_seconds, min(close_seconds, lunch_start_seconds)),
                (max(open_seconds, lunch_end_seconds), close_seconds),
            ]
        return [(window_start, window_end) for window_start, window_end in windows if window_end > window_start]

    @api.model
    def _build_problem(self, routes, drivers, keep_assignment):
        """Return ``(problem, skipped_routes)``.

        ``problem`` = {'vehicles': [...], 'jobs': [...]} with times in seconds since
        local midnight of the route date. ``skipped_routes`` cannot be optimized
        (no coordinates or no valid time window).
        """
        shift_start_seconds = self._hours_to_seconds(float(self._get_setting('shift_start', 7.0)))
        shift_end_seconds = self._hours_to_seconds(float(self._get_setting('shift_end', 19.0)))
        service_seconds = int(self._get_setting('service_minutes', 15)) * 60
        depot_partner = self._get_depot_partner()
        if not self._ensure_coordinates(depot_partner):
            raise UserError(_('Geolocate the depot address (%s) before optimizing.', depot_partner.display_name))
        depot_location = (depot_partner.partner_latitude, depot_partner.partner_longitude)

        vehicle_by_driver = {
            vehicle.driver_employee_id: vehicle
            for vehicle in self.env['fleet.vehicle'].sudo().search([('driver_employee_id', 'in', drivers.ids)])
        }
        problem_vehicles = [{
            'key': driver.id,
            'name': driver.name,
            'vehicle_id': vehicle_by_driver[driver].id,
            'start': depot_location,
            'end': depot_location,
            'time_window': (shift_start_seconds, shift_end_seconds),
        } for driver in drivers if driver in vehicle_by_driver]
        if not problem_vehicles:
            raise UserError(_('None of the selected drivers has a vehicle assigned in Fleet.'))
        available_driver_ids = {problem_vehicle['key'] for problem_vehicle in problem_vehicles}

        located_branches = self._ensure_coordinates(routes.branch_id)
        problem_jobs = []
        skipped_routes = self.env['logistics.route']
        for route in routes:
            time_windows = self._stop_time_windows(route, shift_start_seconds, shift_end_seconds)
            pinned_driver_id = route.driver_employee_id.id if keep_assignment else False
            if (route.branch_id not in located_branches or not time_windows
                    or (pinned_driver_id and pinned_driver_id not in available_driver_ids)):
                skipped_routes |= route
                continue
            problem_jobs.append({
                'key': route.id,
                'location': (route.branch_latitude, route.branch_longitude),
                'service': service_seconds,
                'time_windows': time_windows,
                'allowed_vehicle_keys': [pinned_driver_id] if pinned_driver_id else None,
            })
        return {'vehicles': problem_vehicles, 'jobs': problem_jobs}, skipped_routes

    # ------------------------------------------------------------------
    # VROOM (OpenRouteService or self-hosted)
    # ------------------------------------------------------------------

    @api.model
    def _solve_with_vroom(self, problem):
        vroom_url = self._get_setting('vroom_url') or DEFAULT_VROOM_URL
        routing_profile = self._get_setting('vroom_profile') or 'driving-car'
        api_key = self._get_setting('vroom_api_key')
        # VROOM restricts vehicles per job with "skills": vehicle key = skill id.
        request_payload = {
            'vehicles': [{
                'id': problem_vehicle['key'],
                'profile': routing_profile,
                'description': problem_vehicle['name'],
                'start': [problem_vehicle['start'][1], problem_vehicle['start'][0]],
                'end': [problem_vehicle['end'][1], problem_vehicle['end'][0]],
                'time_window': list(problem_vehicle['time_window']),
                'skills': [problem_vehicle['key']],
            } for problem_vehicle in problem['vehicles']],
            'jobs': [{
                'id': problem_job['key'],
                'location': [problem_job['location'][1], problem_job['location'][0]],
                'service': problem_job['service'],
                'time_windows': [list(time_window) for time_window in problem_job['time_windows']],
                **({'skills': problem_job['allowed_vehicle_keys']} if problem_job['allowed_vehicle_keys'] else {}),
            } for problem_job in problem['jobs']],
        }
        request_headers = {'Content-Type': 'application/json'}
        if api_key:
            request_headers['Authorization'] = api_key
        solution = self._post_json(vroom_url, request_payload, request_headers, provider_name='VROOM')
        if solution.get('code', 0) != 0:
            raise UserError(_('VROOM could not solve the routes: %s', solution.get('error') or solution.get('code')))
        stop_plans = {}
        for solution_route in solution.get('routes', []):
            job_steps = [step for step in solution_route.get('steps', []) if step.get('type') == 'job']
            for step_index, job_step in enumerate(job_steps, start=1):
                stop_plans[job_step['job']] = {
                    'driver_id': solution_route['vehicle'],
                    'sequence': step_index * 10,
                    'arrival_seconds': job_step.get('arrival', 0) + job_step.get('waiting_time', 0),
                }
        unassigned_keys = [unassigned_job['id'] for unassigned_job in solution.get('unassigned', [])]
        return stop_plans, unassigned_keys

    # ------------------------------------------------------------------
    # Google Route Optimization API
    # ------------------------------------------------------------------

    @api.model
    def _get_google_access_token(self, service_account_info):
        """OAuth2 token from a service account key, signed locally (no google-auth dependency)."""
        token_uri = service_account_info.get('token_uri') or GOOGLE_DEFAULT_TOKEN_URI
        issued_at = int(datetime.now(timezone.utc).timestamp())
        jwt_header = {'alg': 'RS256', 'typ': 'JWT'}
        jwt_claims = {
            'iss': service_account_info['client_email'],
            'scope': GOOGLE_SCOPE,
            'aud': token_uri,
            'iat': issued_at,
            'exp': issued_at + SECONDS_PER_HOUR,
        }
        signing_input = '.'.join(
            _base64url(json.dumps(jwt_part, separators=(',', ':')).encode()) for jwt_part in (jwt_header, jwt_claims))
        private_key = serialization.load_pem_private_key(service_account_info['private_key'].encode(), password=None)
        jwt_signature = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        signed_jwt = f'{signing_input}.{_base64url(jwt_signature)}'
        try:
            token_response = requests.post(token_uri, data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': signed_jwt,
            }, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as err:
            _logger.warning('Google OAuth request failed: %s', err)
            raise UserError(_('Could not reach Google to authenticate.')) from None
        if token_response.status_code != 200:
            _logger.warning('Google OAuth error %s: %s', token_response.status_code, token_response.text[:500])
            raise UserError(_('Google rejected the service account credentials.'))
        return token_response.json()['access_token']

    @api.model
    def _solve_with_google(self, problem, local_midnight_utc):
        try:
            service_account_info = json.loads(self._get_setting('google_service_account') or '')
        except ValueError:
            raise UserError(_('Paste the Google service account JSON key in the Logistics settings.')) from None
        project_id = self._get_setting('google_project_id') or service_account_info.get('project_id')
        if not project_id:
            raise UserError(_('Set the Google Cloud project id in the Logistics settings.'))

        def to_timestamp(seconds_since_midnight):
            return (local_midnight_utc + timedelta(seconds=seconds_since_midnight)).strftime('%Y-%m-%dT%H:%M:%SZ')

        def to_lat_lng(location):
            return {'latitude': location[0], 'longitude': location[1]}

        vehicle_index_by_key = {
            problem_vehicle['key']: vehicle_index for vehicle_index, problem_vehicle in enumerate(problem['vehicles'])}
        shift_start_seconds = min(problem_vehicle['time_window'][0] for problem_vehicle in problem['vehicles'])
        shift_end_seconds = max(problem_vehicle['time_window'][1] for problem_vehicle in problem['vehicles'])
        request_payload = {
            'model': {
                'globalStartTime': to_timestamp(shift_start_seconds),
                'globalEndTime': to_timestamp(shift_end_seconds),
                'vehicles': [{
                    'label': str(problem_vehicle['key']),
                    'startLocation': to_lat_lng(problem_vehicle['start']),
                    'endLocation': to_lat_lng(problem_vehicle['end']),
                    'startTimeWindows': [{'startTime': to_timestamp(problem_vehicle['time_window'][0])}],
                    'endTimeWindows': [{'endTime': to_timestamp(problem_vehicle['time_window'][1])}],
                    'costPerKilometer': GOOGLE_COST_PER_KILOMETER,
                    'costPerTraveledHour': GOOGLE_COST_PER_TRAVELED_HOUR,
                } for problem_vehicle in problem['vehicles']],
                'shipments': [{
                    'label': str(problem_job['key']),
                    'deliveries': [{
                        'arrivalLocation': to_lat_lng(problem_job['location']),
                        'duration': f"{problem_job['service']}s",
                        'timeWindows': [{'startTime': to_timestamp(window_start), 'endTime': to_timestamp(window_end)}
                                        for window_start, window_end in problem_job['time_windows']],
                    }],
                    **({'allowedVehicleIndices': [vehicle_index_by_key[vehicle_key]
                                                  for vehicle_key in problem_job['allowed_vehicle_keys']]}
                       if problem_job['allowed_vehicle_keys'] else {}),
                } for problem_job in problem['jobs']],
            },
        }
        access_token = self._get_google_access_token(service_account_info)
        solution = self._post_json(
            GOOGLE_OPTIMIZE_URL.format(project=project_id), request_payload,
            {'Content-Type': 'application/json', 'Authorization': f'Bearer {access_token}'},
            provider_name='Google')
        stop_plans = {}
        for solution_route in solution.get('routes', []):
            # Labels are used because proto3 omits index fields equal to 0.
            for visit_index, route_visit in enumerate(solution_route.get('visits', []), start=1):
                arrival_datetime = datetime.strptime(route_visit['startTime'][:19], '%Y-%m-%dT%H:%M:%S')
                stop_plans[int(route_visit['shipmentLabel'])] = {
                    'driver_id': int(solution_route['vehicleLabel']),
                    'sequence': visit_index * 10,
                    'arrival_seconds': int((arrival_datetime - local_midnight_utc).total_seconds()),
                }
        unassigned_keys = [int(skipped_shipment['label']) for skipped_shipment in solution.get('skippedShipments', [])]
        return stop_plans, unassigned_keys

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    @api.model
    def _post_json(self, url, request_payload, request_headers, provider_name):
        try:
            provider_response = requests.post(
                url, json=request_payload, headers=request_headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as err:
            _logger.warning('%s routing request failed: %s', provider_name, err)
            raise UserError(_('Could not reach the %s routing service.', provider_name)) from None
        if provider_response.status_code != 200:
            _logger.warning('%s routing error %s: %s', provider_name, provider_response.status_code,
                            provider_response.text[:1000])
            raise UserError(_('The %(provider)s routing service answered with error %(status)s.',
                              provider=provider_name, status=provider_response.status_code))
        return provider_response.json()

    @api.model
    def _optimize(self, routes, drivers, route_date, keep_assignment):
        """Compute and write driver, vehicle, sequence and planned arrival of ``routes``.

        Returns ``(planned_routes, unplanned_routes)``.
        """
        problem, skipped_routes = self._build_problem(routes, drivers, keep_assignment)
        if not problem['jobs']:
            return self.env['logistics.route'], routes
        # Shift and opening hours are local to the depot, not to whoever runs the optimization.
        depot_timezone = pytz.timezone(self._get_depot_partner().tz or self.env.user.tz or 'UTC')
        local_midnight_utc = depot_timezone.localize(
            datetime.combine(route_date, datetime.min.time())).astimezone(pytz.utc).replace(tzinfo=None)

        if self._get_setting('routing_provider', 'vroom') == 'google':
            stop_plans, unassigned_keys = self._solve_with_google(problem, local_midnight_utc)
        else:
            stop_plans, unassigned_keys = self._solve_with_vroom(problem)

        vehicle_id_by_driver = {
            problem_vehicle['key']: problem_vehicle['vehicle_id'] for problem_vehicle in problem['vehicles']}
        planned_routes = self.env['logistics.route']
        for route in routes.filtered(lambda route: route.id in stop_plans):
            stop_plan = stop_plans[route.id]
            route.write({
                'driver_employee_id': stop_plan['driver_id'],
                'vehicle_id': vehicle_id_by_driver[stop_plan['driver_id']],
                'sequence': stop_plan['sequence'],
                'planned_arrival': local_midnight_utc + timedelta(seconds=stop_plan['arrival_seconds']),
            })
            planned_routes |= route
        unplanned_routes = skipped_routes | routes.filtered(lambda route: route.id in unassigned_keys)
        return planned_routes, unplanned_routes
