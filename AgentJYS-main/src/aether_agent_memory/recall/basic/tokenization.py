"""Count exactly the rendered ContextPack, using the receiving model's tokenizer."""

from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol


class TokenCounter(Protocol):
    identifier: str

    def count(self, text: str) -> int: ...


class ModelTokenizer:
    def __init__(self, name: str = "o200k_base", path: str | None = None) -> None:
        self.encoding: Any
        self.huggingface = name == "huggingface"
        if self.huggingface:
            from tokenizers import Tokenizer

            if not path:
                raise ValueError("tokenizer path required")
            data = Path(path).read_bytes()
            self.encoding = Tokenizer.from_str(data.decode("utf-8"))
            self.encoding.no_truncation()
            self.encoding.no_padding()
            self.identifier = "hf_json_" + sha256(data).hexdigest()
        else:
            import tiktoken

            self.encoding = tiktoken.get_encoding(name)
            self.identifier = "tiktoken_" + name

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self.huggingface:
            return len(self.encoding.encode(text, add_special_tokens=False).ids)
        # User text containing special-token spellings remains ordinary content.
        return len(self.encoding.encode(text, disallowed_special=()))
