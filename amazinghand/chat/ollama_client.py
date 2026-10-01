"""Ollama's native structured-output API. No cloud client or API key."""
import json
import requests

if __package__:
    from .logic import CHAT_SCHEMA as SCHEMA, SYSTEM_PROMPT, validate_answer
else:
    from logic import CHAT_SCHEMA as SCHEMA, SYSTEM_PROMPT, validate_answer


class OllamaClient:
    def __init__(self, url="http://127.0.0.1:11434", timeout=120):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False

    def request(self, path, body=None):
        try:
            if body is None:
                response = self.session.get(self.url + path, timeout=(5, 10))
            else:
                response = self.session.post(self.url + path, json=body, timeout=(5, self.timeout))
            if not response.ok:
                try:
                    message = response.json().get("error", response.reason)
                except ValueError:
                    message = response.reason
                raise RuntimeError(f"Ollama: {message}")
            return response.json()
        except requests.ConnectionError:
            raise RuntimeError("Cannot connect to Ollama. Open Ollama or run 'ollama serve', then Refresh models.") from None
        except requests.Timeout:
            raise RuntimeError("Ollama timed out. Try again after the model loads, or select a smaller model.") from None

    def models(self):
        return sorted(item["name"] for item in self.request("/api/tags").get("models", []))

    def ask(self, question, model):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError("Enter a question between 1 and 2000 characters.")
        result = self.request("/api/chat", {
            "model": model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT + "\nJSON schema: " + json.dumps(SCHEMA)},
                         {"role": "user", "content": question.strip()}],
            "format": SCHEMA, "options": {"temperature": 0, "num_predict": 1200, "num_ctx": 8192},
        })
        content = result.get("message", {}).get("content")
        if result.get("done") is not True or result.get("done_reason") == "length" or not content:
            raise ValueError("Ollama returned an incomplete answer; no gesture executed.")
        try:
            return validate_answer(json.loads(content))
        except (ValueError, TypeError) as exc:
            raise ValueError("Ollama returned an invalid gesture answer; no movement. Try rephrasing or another model.") from exc

    def chat(self, messages, model, context=""):
        """Multi-turn conversation: messages is a list of {role, content} dicts.

        The system prompt is prepended automatically. Returns a validated Answer.
        """
        if not messages:
            raise ValueError("Conversation history is empty.")
        full_messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + context + "\nJSON schema: " + json.dumps(SCHEMA)},
            *messages,
        ]
        result = self.request("/api/chat", {
            "model": model, "stream": False, "think": False,
            "messages": full_messages,
            "format": SCHEMA, "options": {"temperature": 0, "num_predict": 1200, "num_ctx": 8192},
        })
        content = result.get("message", {}).get("content")
        if result.get("done") is not True or result.get("done_reason") == "length" or not content:
            raise ValueError("Ollama returned an incomplete answer.")
        try:
            answer = validate_answer(json.loads(content))
        except (ValueError, TypeError) as exc:
            raise ValueError("Ollama returned an invalid gesture answer.") from exc
        return answer

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
