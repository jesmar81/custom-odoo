"""Teach ``odoo.addons.ai`` how to talk to OpenAI-compatible providers.

The base module hardcodes two providers inside ``LLMApiService``. Instead of
copying the module, the few provider-dependent methods are wrapped: built-in
providers keep their original code path, any other provider code is resolved
against ``ai.llm.connection`` and spoken to through ``/chat/completions``.
"""
import json
from logging import getLogger

from odoo.exceptions import UserError

from odoo.addons.ai.utils.ai_logging import api_call_logging
from odoo.addons.ai.utils.llm_api_service import LLMApiService

_logger = getLogger(__name__)

BUILTIN_PROVIDERS = ('openai', 'google')
MODEL_SEPARATOR = ':'
REQUEST_TIMEOUT_SECONDS = 120


def _is_custom_provider(service):
    return service.provider not in BUILTIN_PROVIDERS


def _wrap_builtin_or_custom(method_name, custom_implementation):
    """Replace ``LLMApiService.<method_name>`` by a dispatcher."""
    original = getattr(LLMApiService, method_name)

    def dispatcher(self, *args, **kwargs):
        if _is_custom_provider(self):
            return custom_implementation(self, *args, **kwargs)
        return original(self, *args, **kwargs)

    dispatcher.__name__ = method_name
    setattr(LLMApiService, method_name, dispatcher)


def _init(self, env, provider='openai'):
    if provider in BUILTIN_PROVIDERS:
        return _original_init(self, env, provider)
    connection = env['ai.llm.connection']._get_by_code(provider)
    if not connection:
        raise UserError(env._("No AI connection found for provider '%s'.", provider))
    self.provider = provider
    self.env = env
    self.base_url = connection.base_url.rstrip('/')


def _get_api_token(self):
    return self.env['ai.llm.connection']._get_by_code(self.provider)._get_api_key()


def _build_tool_call_response(self, tool_call_id, return_value):
    return {"role": "tool", "tool_call_id": tool_call_id, "content": str(return_value)}


def _handle_api_errors(self, status_code, response):
    if status_code in (401, 403):
        return self.env._("Oops! Your API key seems incorrect. Update it in the AI connection.")
    return None


def _build_user_content(user_prompts, files):
    content = [{"type": "text", "text": prompt} for prompt in user_prompts]
    for file in files or ():
        if file["mimetype"] == "text/plain":
            content.append({"type": "text", "text": file["value"]})
        elif file["mimetype"].startswith("image/"):
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{file['mimetype']};base64,{file['value']}"},
            })
        else:
            raise UserError(f"File type {file['mimetype']} is not supported by this provider.")
    return content


def _request_llm_chat_completions(
    self, llm_model, system_prompts, user_prompts, tools=None,
    files=None, schema=None, temperature=0.5, inputs=(), web_grounding=False,
):
    """Single chat-completions round trip; same contract as ``_request_llm_openai``."""
    model_identifier = llm_model.split(MODEL_SEPARATOR, 1)[1]
    messages = [{"role": "system", "content": "\n\n".join(system_prompts)}]
    messages.extend(inputs or ())
    if user_prompts or files:
        messages.append({"role": "user", "content": _build_user_content(user_prompts, files)})

    body = {"model": model_identifier, "messages": messages, "temperature": temperature}
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "json_schema", "schema": schema},
        }
    if tools:
        body["tools"] = [{
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": tool_parameter_schema,
            },
        } for tool_name, (tool_description, __, __, tool_parameter_schema) in tools.items()]

    with api_call_logging(messages, tools) as record_response:
        llm_response = self._request(
            method="post",
            endpoint="/chat/completions",
            headers=self._get_base_headers(),
            body=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        choices = llm_response.get("choices") or [{}]
        message = choices[0].get("message") or {}

        responses = []
        to_call = []
        next_inputs = list(inputs or ())

        if message.get("tool_calls"):
            next_inputs.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": message["tool_calls"],
            })
            for tool_call in message["tool_calls"]:
                function = tool_call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    _logger.error("AI: Malformed arguments: %s", tool_call)
                    continue
                to_call.append((function.get("name", ""), tool_call.get("id"), arguments))
        elif message.get("content"):
            responses.append(message["content"])

        token_usage = {}
        if usage := llm_response.get("usage"):
            token_usage["input_tokens"] = usage.get("prompt_tokens", 0)
            token_usage["output_tokens"] = usage.get("completion_tokens", 0)
            token_usage["cached_tokens"] = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)

        if record_response:
            record_response(to_call, llm_response, token_usage)
        return responses, to_call, next_inputs


def _pad_embeddings(llm_response, dimensions):
    """Zero-pad shorter vectors to the size of the column; cosine similarity is unchanged."""
    for embedding_row in llm_response.get("data") or ():
        vector = embedding_row["embedding"]
        if len(vector) > dimensions:
            raise UserError(f"The embedding model returned {len(vector)} dimensions, but Odoo stores {dimensions}.")
        embedding_row["embedding"] = vector + [0.0] * (dimensions - len(vector))
    return llm_response


def _get_embedding_custom(self, input, dimensions, model=None, encoding_format=None, user=None):
    connection = self.env['ai.llm.connection']._get_by_code(self.provider)
    if not connection.embedding_model_identifier:
        raise UserError(self.env._("The connection '%s' has no embedding model.", connection.name))
    body = {'input': input, 'model': connection.embedding_model_identifier}
    self._add_if_set(body, 'encoding_format', encoding_format)
    self._add_if_set(body, 'user', user)
    request_kwargs = dict(
        method='post', endpoint='/embeddings', headers=self._get_base_headers(), timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        llm_response = self._request(body=dict(body, dimensions=dimensions), **request_kwargs)
    except UserError as err:
        if "resizable" not in str(err):
            raise
        llm_response = self._request(body=body, **request_kwargs)
    return _pad_embeddings(llm_response, dimensions)


_original_init = LLMApiService.__init__
LLMApiService.__init__ = _init
_wrap_builtin_or_custom('_get_api_token', _get_api_token)
_wrap_builtin_or_custom('_build_tool_call_response', _build_tool_call_response)
_wrap_builtin_or_custom('_handle_api_errors', _handle_api_errors)
_wrap_builtin_or_custom('_request_llm', _request_llm_chat_completions)
_wrap_builtin_or_custom('get_embedding', _get_embedding_custom)
