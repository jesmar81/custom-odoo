import hmac
import logging

from odoo import http
from odoo.http import request

from ..models.res_config_settings import WEBHOOK_TOKEN_PARAM

_logger = logging.getLogger(__name__)

TEXT_MESSAGE_TYPES = ('conversation', 'extendedTextMessage')
GROUP_JID_SUFFIX = '@g.us'
PHONE_JID_SUFFIX = '@s.whatsapp.net'


def _normalize_event_name(event_name):
    """Evolution API versions emit 'MESSAGES_UPSERT' or 'messages.upsert'."""
    return (event_name or '').lower().replace('_', '.')


def _extract_text(message_content, message_type):
    message_content = message_content or {}
    text = message_content.get('conversation') or (message_content.get('extendedTextMessage') or {}).get('text')
    if text:
        return text
    # Quotations are often sent as photos or PDFs: keep a trace so a person looks at the phone.
    return f"[{message_type or 'attachment'}] {message_content.get('caption') or ''}".strip()


class EvolutionWebhook(http.Controller):

    @http.route('/evolution_api/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def evolution_webhook(self, token=None, **_query):
        """Receive Evolution API events. Plain HTTP route: Evolution posts raw JSON, not JSON-RPC."""
        expected_token = request.env['ir.config_parameter'].sudo().get_param(WEBHOOK_TOKEN_PARAM)
        if not expected_token or not token or not hmac.compare_digest(str(token), expected_token):
            return request.make_json_response({'error': 'forbidden'}, status=403)

        try:
            payload = request.get_json_data()
        except ValueError:
            return request.make_json_response({'error': 'invalid json'}, status=400)
        if not isinstance(payload, dict) or _normalize_event_name(payload.get('event')) != 'messages.upsert':
            return request.make_json_response({'status': 'ignored'})

        settings = request.env['evolution.message']._get_settings()
        if payload.get('instance') and settings['instance'] and payload['instance'] != settings['instance']:
            return request.make_json_response({'status': 'ignored'})

        events = payload.get('data')
        for event in events if isinstance(events, list) else [events]:
            self._process_incoming_message(event or {})
        return request.make_json_response({'status': 'ok'})

    def _process_incoming_message(self, event):
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
        body = _extract_text(event.get('message'), event.get('messageType'))
        request.env['evolution.message']._register_incoming(
            remote_jid.split('@')[0], body, external_id=key.get('id'),
        )
