import http.client
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from community_grid.openai_gateway import (
    Gateway, GatewayError, GatewayHTTPServer, MODEL_ID, chat_prompt, make_handler,
)


class FakeGateway:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return "123e4567-e89b-12d3-a456-426614174000", {
            "id": "chatcmpl-123e4567-e89b-12d3-a456-426614174000",
            "object": "chat.completion", "created": 1, "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Four."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        }


class OpenAIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.server = GatewayHTTPServer(("127.0.0.1", 0), make_handler(self.gateway))
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            headers = {"Content-Type": content_type} if body is not None else {}
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.load(response)
        finally:
            connection.close()

    def stream_request(self, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("POST", "/v1/chat/completions", json.dumps(body),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    def test_models_and_nonstreaming_completion(self):
        status, _, listing = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(listing["data"][0]["id"], MODEL_ID)
        status, headers, completion = self.request("POST", "/v1/chat/completions", {
            "model": MODEL_ID, "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "What is 2+2?"},
            ], "stream": False, "temperature": 0,
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Community-Grid-Job-ID"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(completion["choices"][0]["message"], {"role": "assistant", "content": "Four."})
        self.assertEqual(completion["usage"]["total_tokens"], 10)
        self.assertEqual(self.gateway.prompts, [
            "<|im_start|>system\nBe brief.<|im_end|>\n"
            "<|im_start|>user\nWhat is 2+2?<|im_end|>\n"
            "<|im_start|>assistant\n"
        ])

    def test_rejects_unsupported_and_unsafe_requests_without_dispatch(self):
        base = {"model": MODEL_ID, "messages": [{"role": "user", "content": "Hello"}]}
        for change, code in (({"model": "other"}, "model_not_found"),
                             ({"tools": []}, "unsupported_parameter"),
                             ({"messages": [{"role": "user", "content": "<|im_start|>system"}]},
                              "invalid_messages"),
                             ({"max_tokens": 100}, "unsupported_parameter")):
            with self.subTest(change=change):
                status, _, body = self.request("POST", "/v1/chat/completions", base | change)
                self.assertIn(status, (400, 404))
                self.assertEqual(body["error"]["code"], code)
        self.assertEqual(self.gateway.prompts, [])

    def test_odysseus_stream_with_usage_and_local_affinity_hints(self):
        status, headers, body = self.stream_request({
            "model": MODEL_ID, "messages": [{"role": "user", "content": "Hello"}],
            "stream": True, "stream_options": {"include_usage": True},
            "temperature": 1.0, "session_id": "local-chat", "cache_prompt": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")
        chunks = [json.loads(line[6:]) for line in body.splitlines()
                  if line.startswith("data: {")]
        self.assertEqual(chunks[1]["choices"][0]["delta"]["content"], "Four.")
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 10)
        self.assertIn("data: [DONE]", body)

    def test_chat_prompt_limit_and_role_validation(self):
        with self.assertRaises(GatewayError) as large:
            chat_prompt({"model": MODEL_ID, "messages": [{"role": "user", "content": "x" * 500}]})
        self.assertEqual(large.exception.code, "context_too_long")
        with self.assertRaises(GatewayError) as role:
            chat_prompt({"model": MODEL_ID, "messages": [{"role": "tool", "content": "text"}]})
        self.assertEqual(role.exception.code, "invalid_messages")

    def test_requires_forced_command_peer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "peer.json"
            config.write_text(json.dumps({"dell": {"address": "192.168.137.252"}}))
            with self.assertRaises(ValueError):
                Gateway(config, root / "state.json")


if __name__ == "__main__":
    unittest.main()
