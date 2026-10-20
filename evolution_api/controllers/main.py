import base64
import hmac
import logging
import mimetypes

import requests

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

GROUP_JID_SUFFIX = '@g.us'
PHONE_JID_SUFFIX = '@s.whatsapp.net'
MEDIA_MESSAGE_TYPES = ('imageMessage', 'documentMessage', 'audioMessage', 'videoMessage', 'stickerMessage')
DEFAULT_MAX_FILE_MB = 16


def _normalize_event_name(event_name):
    """Evolution API versions emit 'MESSAGES_UPSERT' or 'messages.upsert'."""
    return (event_name or '').lower().replace('_', '.')


class EvolutionWebhook(http.Controller):

    @http.route('/evolution_api/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def evolution_webhook(self, token=None, **_query):
        """Plain HTTP route: Evolution posts raw JSON, not JSON-RPC. The token identifies the account."""
        account = self._get_account_by_token(token)
        if not account:
            return request.make_json_response({'error': 'forbidden'}, status=403)
        try:
            payload = request.get_json_data()
        except ValueError:
            return request.make_json_response({'error': 'invalid json'}, status=400)
        if not isinstance(payload, dict):
            return request.make_json_response({'status': 'ignored'})
        if payload.get('instance') and payload['instance'] != account.instance:
            return request.make_json_response({'status': 'ignored'})

        event_name = _normalize_event_name(payload.get('event'))
        if event_name == 'connection.update':
            state = (payload.get('data') or {}).get('state')
            if isinstance(state, str):
                account._update_connection_state(state)
        elif event_name == 'messages.upsert':
            events = payload.get('data')
            for event in events if isinstance(events, list) else [events]:
                self._process_incoming_message(account, event or {})
        return request.make_json_response({'status': 'ok'})

    @staticmethod
    def _get_account_by_token(token):
        if not token:
            return None
        accounts = request.env['evolution.account'].sudo().search([])
        for account in accounts:
            if account.webhook_token and hmac.compare_digest(str(token), account.webhook_token):
                return account
        return None

    def _process_incoming_message(self, account, event):
        key = event.get('key') or {}
        remote_jid = key.get('remoteJid') or ''
        if key.get('fromMe') or remote_jid.endswith(GROUP_JID_SUFFIX):
            return
        if not remote_jid.endswith(PHONE_JID_SUFFIX):
            # WhatsApp may hide the number behind an '@lid' identifier; use the alternative JID if present.
            remote_jid = key.get('remoteJidAlt') or event.get('senderPn') or ''
            if not remote_jid.endswith(PHONE_JID_SUFFIX):
                _logger.info("Evolution API: message ignored, no phone number in %s", key)
                return
        message_content = event.get('message') or {}
        body = message_content.get('conversation') or (message_content.get('extendedTextMessage') or {}).get('text')
        files = []
        for media_type in MEDIA_MESSAGE_TYPES:
            if media_type in message_content:
                body = body or message_content[media_type].get('caption')
                file = self._extract_file(account, event, media_type, message_content)
                if file:
                    files.append(file)
                else:
                    body = (body or '') + f" [{media_type}: could not download]"
        request.env['evolution.message']._register_incoming(
            account, remote_jid.split('@')[0], body or '', external_id=key.get('id'), files=files,
        )

    def _extract_file(self, account, event, media_type, message_content):
        """Return (name, mimetype, bytes) of a received file, or None."""
        media = message_content[media_type]
        mimetype = media.get('mimetype') or 'application/octet-stream'
        encoded = message_content.get('base64') or event.get('base64')
        if not encoded:
            try:
                answer = account._api_request('POST', '/chat/getBase64FromMediaMessage', {'message': event})
                encoded = answer.get('base64')
                mimetype = answer.get('mimetype') or mimetype
            except (requests.exceptions.RequestException, ValueError) as err:
                _logger.info("Evolution API: media download failed: %s", err)
                return None
        if not encoded:
            return None
        max_bytes = int(request.env['ir.config_parameter'].sudo().get_param('evolution_api.max_file_mb') or DEFAULT_MAX_FILE_MB) * 1024 * 1024
        try:
            raw = base64.b64decode(encoded.split(',')[-1])
        except ValueError:
            return None
        if len(raw) > max_bytes:
            return None
        extension = mimetypes.guess_extension(mimetype.split(';')[0]) or ''
        return media.get('fileName') or f"{media_type}{extension}", mimetype, raw
