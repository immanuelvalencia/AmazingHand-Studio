"""Compact, streaming Ollama responses; motor plans still validate in full."""
from copy import deepcopy
from dataclasses import replace
import json
import re
import time

import requests
from amazinghand.chat.cancellation import Cancelled
from amazinghand.chat.logic import CHAT_SCHEMA, validate_answer
from amazinghand.chat.ollama_client import OllamaClient

from .command_inference import (COMMAND_SCHEMA, COMMAND_PROMPT, INFERENCE_VERSION, validate_action_fields)

FAST_SCHEMA = deepcopy(COMMAND_SCHEMA)
FAST_PROMPT = COMMAND_PROMPT



def partial_answer(content):
    """Decode only the first JSON string, including incomplete escaped chunks."""
    match = re.match(r'^\s*\{\s*"answer"\s*:\s*"', content)
    if match is None:
        return ""
    tail = content[match.end():]
    escaped = False
    for index, char in enumerate(tail):
        if char == '"' and not escaped:
            tail = tail[:index]
            break
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    for trim in range(min(7, len(tail) + 1)):
        try:
            decoded = json.loads('"' + (tail[:-trim] if trim else tail) + '"')
            # Wait for the low surrogate before showing a split Unicode pair.
            while decoded and 0xD800 <= ord(decoded[-1]) <= 0xDBFF:
                decoded = decoded[:-1]
            return decoded
        except ValueError:
            continue
    return ""


class FastOllamaClient(OllamaClient):
    on_text = None
    cancel = None
    last_metrics = None

    def warm(self, model):
        return self.request("/api/chat", {"model": model, "messages": [], "stream": False,
                                          "keep_alive": "30m", "options": {"num_ctx": 8192}})

    def chat(self, messages, model, context=""):
        self.last_raw = ""
        self.last_metrics = None
        if not messages:
            raise ValueError("Conversation history is empty.")
        body = {"model": model, "stream": True, "think": False, "keep_alive": "30m",
                "messages": [{"role": "system", "content": FAST_PROMPT + "\n" + context}, *messages],
                "format": FAST_SCHEMA, "options": {"temperature": 0, "num_predict": 768, "num_ctx": 8192}}
        content, shown, final = "", "", None
        start = time.perf_counter()
        first_text = None
        try:
            with self.session.post(self.url + "/api/chat", json=body, stream=True, timeout=(5, self.timeout)) as response:
                if not response.ok:
                    raise RuntimeError(f"Ollama: {response.text[:300]}")
                for line in response.iter_lines(chunk_size=1):
                    if self.cancel is not None and self.cancel.is_set():
                        raise Cancelled()
                    if time.perf_counter() - start > self.timeout:
                        raise RuntimeError("Ollama response exceeded the time limit.")
                    if not line:
                        continue
                    packet = json.loads(line)
                    if packet.get("error"):
                        raise RuntimeError("Ollama: " + str(packet["error"]))
                    content += packet.get("message", {}).get("content", "")
                    self.last_raw = content
                    if len(content) > 20000:
                        raise ValueError("Ollama response is too large; no movement.")
                    text = partial_answer(content)
                    if text and text != shown:
                        shown = text
                        if first_text is None:
                            first_text = time.perf_counter() - start
                        if self.on_text:
                            self.on_text(text)
                    if packet.get("done"):
                        final = packet
                        break
        except requests.ConnectionError:
            raise RuntimeError("Cannot connect to Ollama. Open Ollama and Refresh models.") from None
        except requests.Timeout:
            raise RuntimeError("Ollama timed out. Try a smaller installed model.") from None
        if not final or final.get("done_reason") == "length":
            raise ValueError("Ollama returned an incomplete answer; no movement.")
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()
        self.last_metrics = {"total_seconds": time.perf_counter() - start, "first_text_seconds": first_text,
                             "tokens": final.get("eval_count"), "load_seconds": final.get("load_duration", 0) / 1e9}
        payload = json.loads(content)
        validate_action_fields(payload)
        answer = validate_answer(payload)
        # A bare yes/no reply has an unambiguous hand meaning even if a small
        # model copies the generic explanation gesture from the format example.
        binary = answer.text.lower().strip(".! ")
        if answer.action == "point" and not answer.steps and binary in ("yes", "no"):
            answer = replace(answer, action=binary)
        return answer
