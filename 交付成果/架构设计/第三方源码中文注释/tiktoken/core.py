# 【中文研读】本文件重点注释 Encoding 初始化、常规/批量编码和解码路径。BPE 数值实现位于 Rust 扩展；其他底层工具保留原样。P3 用 len(token_ids) 计数，不能用中文字数、字节数或 Embedding tokenizer 代替。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
from __future__ import annotations

import functools
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, AbstractSet, Collection, Literal, NoReturn, Sequence

from tiktoken import _tiktoken

if TYPE_CHECKING:
    import re

    import numpy as np
    import numpy.typing as npt


# 【中文研读】类型职责：绑定编码表、特殊 token 和 BPE 核心的对象；同一文本换编码表后 token 数可能变化。
class Encoding:
    # 【中文研读】方法职责：将词表和分段规则装配为编码器
    # 【中文研读】输入参数：name（工具或组件名称）；pat_str（BPE 前用于分段的正则）；mergeable_ranks（字节片段到 token ID/合并优先级的词表）；special_tokens（特殊字符串到 token ID 的映射）；explicit_n_vocab（可选的词表大小断言）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        name: str,
        *,
        pat_str: str,
        mergeable_ranks: dict[bytes, int],
        special_tokens: dict[str, int],
        explicit_n_vocab: int | None = None,
    ):
        """Creates an Encoding object.

        See openai_public.py for examples of how to construct an Encoding object.

        Args:
            name: The name of the encoding. It should be clear from the name of the encoding
                what behaviour to expect, in particular, encodings with different special tokens
                should have different names.
            pat_str: A regex pattern string that is used to split the input text.
            mergeable_ranks: A dictionary mapping mergeable token bytes to their ranks. The ranks
                must correspond to merge priority.
            special_tokens: A dictionary mapping special token strings to their token values.
            explicit_n_vocab: The number of tokens in the vocabulary. If provided, it is checked
                that the number of mergeable tokens and special tokens is equal to this number.
        """
        # 【中文研读】处理流程：保存规则，计算最大 token ID，按可选词表大小做断言，准备特殊 ID 集合，再建立 Rust CoreBPE 实例。
        self.name = name

        self._pat_str = pat_str
        self._mergeable_ranks = mergeable_ranks
        self._special_tokens = special_tokens

        self.max_token_value = max(
            max(mergeable_ranks.values()), max(special_tokens.values(), default=0)
        )
        if explicit_n_vocab:
            assert len(mergeable_ranks) + len(special_tokens) == explicit_n_vocab
            assert self.max_token_value == explicit_n_vocab - 1

        # Contains on set is significantly faster than on dict_values
        self._special_token_values = set(self._special_tokens.values())

        # 【中文研读】阶段 1：连接高性能 Rust 实现，Python 层保留参数与策略；此对象既不是语言模型，也不生成语义向量。
        self._core_bpe = _tiktoken.CoreBPE(mergeable_ranks, special_tokens, pat_str)

    def __repr__(self) -> str:
        return f"<Encoding {self.name!r}>"

    # ====================
    # Encoding
    # ====================

    # 【中文研读】方法职责：把全部输入按普通文本编码
    # 【中文研读】输入参数：text（待编码或解码的文本参数，具体类型见签名）。
    # 【中文研读】返回约定：list[int]。结果的业务含义与失败分支见下面处理流程。
    def encode_ordinary(self, text: str) -> list[int]:
        """Encodes a string into tokens, ignoring special tokens.

        This is equivalent to `encode(text, disallowed_special=())` (but slightly faster).

        ```
        >>> enc.encode_ordinary("hello world")
        [31373, 995]
        """
        # 【中文研读】处理流程：直接调用 Rust 编码，不把特殊标记当控制 token；遇到非法 Unicode 代理项时替换修复后重试，返回 token ID 列表。
        try:
            return self._core_bpe.encode_ordinary(text)
        # 【中文研读】失败分支：修复异常代理项后重试。修复可能改变原始字符串，不能宣称 encode/decode 对所有非法 Unicode 都无损。
        except UnicodeEncodeError:
            # See comment in encode
            text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
            return self._core_bpe.encode_ordinary(text)

    # 【中文研读】方法职责：依据特殊 token 策略把字符串编码
    # 【中文研读】输入参数：text（待编码或解码的文本参数，具体类型见签名）；allowed_special（允许作为专用 token 解释的字符串集合）；disallowed_special（遇到后需要报错的特殊字符串集合）。
    # 【中文研读】返回约定：list[int]。结果的业务含义与失败分支见下面处理流程。
    def encode(
        self,
        text: str,
        *,
        allowed_special: Literal["all"] | AbstractSet[str] = set(),  # noqa: B006
        disallowed_special: Literal["all"] | Collection[str] = "all",
    ) -> list[int]:
        """Encodes a string into tokens.

        Special tokens are artificial tokens used to unlock capabilities from a model,
        such as fill-in-the-middle. So we want to be careful about accidentally encoding special
        tokens, since they can be used to trick a model into doing something we don't want it to do.

        Hence, by default, encode will raise an error if it encounters text that corresponds
        to a special token. This can be controlled on a per-token level using the `allowed_special`
        and `disallowed_special` parameters. In particular:
        - Setting `disallowed_special` to () will prevent this function from raising errors and
          cause all text corresponding to special tokens to be encoded as natural text.
        - Setting `allowed_special` to "all" will cause this function to treat all text
          corresponding to special tokens to be encoded as special tokens.

        ```
        >>> enc.encode("hello world")
        [31373, 995]
        >>> enc.encode("<|endoftext|>", allowed_special={"<|endoftext|>"})
        [50256]
        >>> enc.encode("<|endoftext|>", allowed_special="all")
        [50256]
        >>> enc.encode("<|endoftext|>")
        # Raises ValueError
        >>> enc.encode("<|endoftext|>", disallowed_special=())
        [27, 91, 437, 1659, 5239, 91, 29]
        ```
        """
        # 【中文研读】处理流程：展开允许/拒绝集合，先扫描禁止的特殊字符串并报错，再进入 Rust BPE；非法 Unicode 修复后重试，结果长度是此编码口径的 token 数。
        # 【中文研读】编码阶段 A：决定哪些字面标记允许作为特殊 token；P3 应显式确定普通用户文本的计数策略，避免数据含标记就意外报错。
        if allowed_special == "all":
            allowed_special = self.special_tokens_set
        if disallowed_special == "all":
            disallowed_special = self.special_tokens_set - allowed_special
        if disallowed_special:
            if not isinstance(disallowed_special, frozenset):
                disallowed_special = frozenset(disallowed_special)
            if match := _special_token_regex(disallowed_special).search(text):
                raise_disallowed_special_token(match.group())

        try:
            # 【中文研读】编码阶段 B：返回整数 ID 列表。要计算预算取 len(ids)，而不是把 IDs 求和；预算还应覆盖最终序列化的分隔符和来源信息。
            return self._core_bpe.encode(text, allowed_special)
        # 【中文研读】失败分支：修复异常代理项后重试。修复可能改变原始字符串，不能宣称 encode/decode 对所有非法 Unicode 都无损。
        except UnicodeEncodeError:
            # BPE operates on bytes, but the regex operates on unicode. If we pass a str that is
            # invalid UTF-8 to Rust, it will rightfully complain. Here we do a quick and dirty
            # fixup for any surrogate pairs that may have sneaked their way into the text.
            # Technically, this introduces a place where encode + decode doesn't roundtrip a Python
            # string, but given that this is input we want to support, maybe that's okay.
            # Also we use errors="replace" to handle weird things like lone surrogates.
            text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
            # 【中文研读】编码阶段 B：返回整数 ID 列表。要计算预算取 len(ids)，而不是把 IDs 求和；预算还应覆盖最终序列化的分隔符和来源信息。
            return self._core_bpe.encode(text, allowed_special)

    def encode_to_numpy(
        self,
        text: str,
        *,
        allowed_special: Literal["all"] | AbstractSet[str] = set(),  # noqa: B006
        disallowed_special: Literal["all"] | Collection[str] = "all",
    ) -> npt.NDArray[np.uint32]:
        """Encodes a string into tokens, returning a numpy array.

        Avoids the overhead of copying the token buffer into a Python list.
        """
        # 【中文研读】编码阶段 A：决定哪些字面标记允许作为特殊 token；P3 应显式确定普通用户文本的计数策略，避免数据含标记就意外报错。
        if allowed_special == "all":
            allowed_special = self.special_tokens_set
        if disallowed_special == "all":
            disallowed_special = self.special_tokens_set - allowed_special
        if disallowed_special:
            if not isinstance(disallowed_special, frozenset):
                disallowed_special = frozenset(disallowed_special)
            if match := _special_token_regex(disallowed_special).search(text):
                raise_disallowed_special_token(match.group())

        import numpy as np

        buffer = self._core_bpe.encode_to_tiktoken_buffer(text, allowed_special)
        return np.frombuffer(buffer, dtype=np.uint32)

    # 【中文研读】方法职责：并行编码多段普通文本
    # 【中文研读】输入参数：text（待编码或解码的文本参数，具体类型见签名）；num_threads（批量分词线程数）。
    # 【中文研读】返回约定：list[list[int]]。结果的业务含义与失败分支见下面处理流程。
    def encode_ordinary_batch(self, text: list[str], *, num_threads: int = 8) -> list[list[int]]:
        """Encodes a list of strings into tokens, in parallel, ignoring special tokens.

        This is equivalent to `encode_batch(text, disallowed_special=())` (but slightly faster).

        ```
        >>> enc.encode_ordinary_batch(["hello world", "goodbye world"])
        [[31373, 995], [11274, 16390, 995]]
        ```
        """
        # 【中文研读】处理流程：在线程池对每项调用 encode_ordinary，map 保持输入顺序；不是把各文本先拼在一起编码。
        encoder = functools.partial(self.encode_ordinary)
        with ThreadPoolExecutor(num_threads) as e:
            return list(e.map(encoder, text))

    # 【中文研读】方法职责：按相同特殊 token 策略编码多段文本
    # 【中文研读】输入参数：text（待编码或解码的文本参数，具体类型见签名）；num_threads（批量分词线程数）；allowed_special（允许作为专用 token 解释的字符串集合）；disallowed_special（遇到后需要报错的特殊字符串集合）。
    # 【中文研读】返回约定：list[list[int]]。结果的业务含义与失败分支见下面处理流程。
    def encode_batch(
        self,
        text: list[str],
        *,
        num_threads: int = 8,
        allowed_special: Literal["all"] | AbstractSet[str] = set(),  # noqa: B006
        disallowed_special: Literal["all"] | Collection[str] = "all",
    ) -> list[list[int]]:
        """Encodes a list of strings into tokens, in parallel.

        See `encode` for more details on `allowed_special` and `disallowed_special`.

        ```
        >>> enc.encode_batch(["hello world", "goodbye world"])
        [[31373, 995], [11274, 16390, 995]]
        ```
        """
        # 【中文研读】处理流程：规范化允许/拒绝集合，构造固定选项的 encode 调用，并在线程池 map 后按原顺序返回。
        # 【中文研读】编码阶段 A：决定哪些字面标记允许作为特殊 token；P3 应显式确定普通用户文本的计数策略，避免数据含标记就意外报错。
        if allowed_special == "all":
            allowed_special = self.special_tokens_set
        if disallowed_special == "all":
            disallowed_special = self.special_tokens_set - allowed_special
        if not isinstance(disallowed_special, frozenset):
            disallowed_special = frozenset(disallowed_special)

        encoder = functools.partial(
            self.encode, allowed_special=allowed_special, disallowed_special=disallowed_special
        )
        with ThreadPoolExecutor(num_threads) as e:
            return list(e.map(encoder, text))

    def encode_with_unstable(
        self,
        text: str,
        *,
        allowed_special: Literal["all"] | AbstractSet[str] = set(),  # noqa: B006
        disallowed_special: Literal["all"] | Collection[str] = "all",
    ) -> tuple[list[int], list[list[int]]]:
        """Encodes a string into stable tokens and possible completion sequences.

        Note that the stable tokens will only represent a substring of `text`.

        See `encode` for more details on `allowed_special` and `disallowed_special`.

        This API should itself be considered unstable.

        ```
        >>> enc.encode_with_unstable("hello fanta")
        ([31373], [(277, 4910), (5113, 265), ..., (8842,)])

        >>> text = "..."
        >>> stable_tokens, completions = enc.encode_with_unstable(text)
        >>> assert text.encode().startswith(enc.decode_bytes(stable_tokens))
        >>> assert all(enc.decode_bytes(stable_tokens + seq).startswith(text.encode()) for seq in completions)
        ```
        """
        # 【中文研读】编码阶段 A：决定哪些字面标记允许作为特殊 token；P3 应显式确定普通用户文本的计数策略，避免数据含标记就意外报错。
        if allowed_special == "all":
            allowed_special = self.special_tokens_set
        if disallowed_special == "all":
            disallowed_special = self.special_tokens_set - allowed_special
        if disallowed_special:
            if not isinstance(disallowed_special, frozenset):
                disallowed_special = frozenset(disallowed_special)
            if match := _special_token_regex(disallowed_special).search(text):
                raise_disallowed_special_token(match.group())

        return self._core_bpe.encode_with_unstable(text, allowed_special)

    def encode_single_token(self, text_or_bytes: str | bytes) -> int:
        """Encodes text corresponding to a single token to its token value.

        NOTE: this will encode all special tokens.

        Raises `KeyError` if the token is not in the vocabulary.

        ```
        >>> enc.encode_single_token("hello")
        31373
        ```
        """
        if isinstance(text_or_bytes, str):
            text_or_bytes = text_or_bytes.encode("utf-8")
        return self._core_bpe.encode_single_token(text_or_bytes)

    # ====================
    # Decoding
    # ====================

    # 【中文研读】方法职责：将 token ID 序列还原为字节
    # 【中文研读】输入参数：tokens（需要解码的 token ID 序列）。
    # 【中文研读】返回约定：bytes。结果的业务含义与失败分支见下面处理流程。
    def decode_bytes(self, tokens: Sequence[int]) -> bytes:
        """Decodes a list of tokens into bytes.

        ```
        >>> enc.decode_bytes([31373, 995])
        b'hello world'
        ```
        """
        # 【中文研读】处理流程：委托 CoreBPE 解码，尚未按 UTF-8 转成字符串；单个 token 可能只是汉字的部分字节。
        return self._core_bpe.decode_bytes(tokens)

    # 【中文研读】方法职责：将 token ID 序列转回文本
    # 【中文研读】输入参数：tokens（需要解码的 token ID 序列）；errors（UTF-8 解码错误处理策略）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def decode(self, tokens: Sequence[int], errors: str = "replace") -> str:
        """Decodes a list of tokens into a string.

        WARNING: the default behaviour of this function is lossy, since decoded bytes are not
        guaranteed to be valid UTF-8. You can control this behaviour using the `errors` parameter,
        for instance, setting `errors=strict`.

        ```
        >>> enc.decode([31373, 995])
        'hello world'
        ```
        """
        # 【中文研读】处理流程：先还原字节，再按 errors 参数处理 UTF-8 解码错误。按 token 数截断后可能落在字符字节中间，P3 要检查最终文本并重新计数。
        return self._core_bpe.decode_bytes(tokens).decode("utf-8", errors=errors)

    def decode_single_token_bytes(self, token: int) -> bytes:
        """Decodes a token into bytes.

        NOTE: this will decode all special tokens.

        Raises `KeyError` if the token is not in the vocabulary.

        ```
        >>> enc.decode_single_token_bytes(31373)
        b'hello'
        ```
        """
        return self._core_bpe.decode_single_token_bytes(token)

    def decode_tokens_bytes(self, tokens: Sequence[int]) -> list[bytes]:
        """Decodes a list of tokens into a list of bytes.

        Useful for visualising tokenisation.
        >>> enc.decode_tokens_bytes([31373, 995])
        [b'hello', b' world']
        """
        return [self.decode_single_token_bytes(token) for token in tokens]

    def decode_with_offsets(self, tokens: Sequence[int]) -> tuple[str, list[int]]:
        """Decodes a list of tokens into a string and a list of offsets.

        Each offset is the index into text corresponding to the start of each token.
        If UTF-8 character boundaries do not line up with token boundaries, the offset is the index
        of the first character that contains bytes from the token.

        This will currently raise if given tokens that decode to invalid UTF-8; this behaviour may
        change in the future to be more permissive.

        >>> enc.decode_with_offsets([31373, 995])
        ('hello world', [0, 5])
        """
        token_bytes = self.decode_tokens_bytes(tokens)

        text_len = 0
        offsets = []
        for token in token_bytes:
            offsets.append(max(0, text_len - (0x80 <= token[0] < 0xC0)))
            text_len += sum(1 for c in token if not 0x80 <= c < 0xC0)

        # TODO: assess correctness for errors="ignore" and errors="replace"
        text = b"".join(token_bytes).decode("utf-8", errors="strict")
        return text, offsets

    def decode_batch(
        self, batch: Sequence[Sequence[int]], *, errors: str = "replace", num_threads: int = 8
    ) -> list[str]:
        """Decodes a batch (list of lists of tokens) into a list of strings."""
        decoder = functools.partial(self.decode, errors=errors)
        with ThreadPoolExecutor(num_threads) as e:
            return list(e.map(decoder, batch))

    def decode_bytes_batch(
        self, batch: Sequence[Sequence[int]], *, num_threads: int = 8
    ) -> list[bytes]:
        """Decodes a batch (list of lists of tokens) into a list of bytes."""
        with ThreadPoolExecutor(num_threads) as e:
            return list(e.map(self.decode_bytes, batch))

    # ====================
    # Miscellaneous
    # ====================

    def token_byte_values(self) -> list[bytes]:
        """Returns the list of all token byte values."""
        return self._core_bpe.token_byte_values()

    @property
    def eot_token(self) -> int:
        return self._special_tokens["<|endoftext|>"]

    @functools.cached_property
    def special_tokens_set(self) -> set[str]:
        return set(self._special_tokens.keys())

    def is_special_token(self, token: int) -> bool:
        assert isinstance(token, int)
        return token in self._special_token_values

    @property
    def n_vocab(self) -> int:
        """For backwards compatibility. Prefer to use `enc.max_token_value + 1`."""
        return self.max_token_value + 1

    # ====================
    # Private
    # ====================

    def _encode_single_piece(self, text_or_bytes: str | bytes) -> list[int]:
        """Encodes text corresponding to bytes without a regex split.

        NOTE: this will not encode any special tokens.

        ```
        >>> enc.encode_single_piece("helloqqqq")
        [31373, 38227, 38227]
        ```
        """
        if isinstance(text_or_bytes, str):
            text_or_bytes = text_or_bytes.encode("utf-8")
        return self._core_bpe.encode_single_piece(text_or_bytes)

    def _encode_only_native_bpe(self, text: str) -> list[int]:
        """Encodes a string into tokens, but do regex splitting in Python."""
        # We need specifically `regex` in order to compile pat_str due to e.g. \p
        import regex

        _unused_pat = regex.compile(self._pat_str)
        ret = []
        for piece in regex.findall(_unused_pat, text):
            ret.extend(self._core_bpe.encode_single_piece(piece.encode("utf-8")))
        return ret

    def _encode_bytes(self, text: bytes) -> list[int]:
        return self._core_bpe._encode_bytes(text)

    def __getstate__(self) -> object:
        import tiktoken.registry

        # As an optimisation, pickle registered encodings by reference
        if self is tiktoken.registry.ENCODINGS.get(self.name):
            return self.name
        return {
            "name": self.name,
            "pat_str": self._pat_str,
            "mergeable_ranks": self._mergeable_ranks,
            "special_tokens": self._special_tokens,
        }

    def __setstate__(self, value: object) -> None:
        import tiktoken.registry

        if isinstance(value, str):
            self.__dict__ = tiktoken.registry.get_encoding(value).__dict__
            return
        self.__init__(**value)


@functools.lru_cache(maxsize=128)
def _special_token_regex(tokens: frozenset[str]) -> re.Pattern[str]:
    try:
        import regex as re
    except ImportError:
        import re
    inner = "|".join(re.escape(token) for token in tokens)
    return re.compile(f"({inner})")


def raise_disallowed_special_token(token: str) -> NoReturn:
    raise ValueError(
        f"Encountered text corresponding to disallowed special token {token!r}.\n"
        "If you want this text to be encoded as a special token, "
        f"pass it to `allowed_special`, e.g. `allowed_special={{{token!r}, ...}}`.\n"
        f"If you want this text to be encoded as normal text, disable the check for this token "
        f"by passing `disallowed_special=(enc.special_tokens_set - {{{token!r}}})`.\n"
        "To disable this check for all special tokens, pass `disallowed_special=()`.\n"
    )
