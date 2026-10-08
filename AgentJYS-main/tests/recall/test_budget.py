"""AET-21 RC-BUD-06–08: real tokenizer configuration and failure boundaries."""

import builtins

import pytest
import tiktoken

from aether_agent_memory.recall.basic.tokenization import ModelTokenizer
from recall_budget_support import write_hf_tokenizer


@pytest.mark.p0
@pytest.mark.parametrize(
    "text",
    [
        "hello world",
        "用户喜欢无糖咖啡",
        "👩🏽‍💻🙂",
        "café e\u0301",
        "中 English\n第二行🙂",
        "<|endoftext|>",
    ],
)
def test_rc_bud_06_unicode_and_special_spellings_use_actual_pack_tokens(text):
    rendered = "[1] " + text + "\nSources: source_1@1\n"
    expected = len(tiktoken.get_encoding("o200k_base").encode(rendered, disallowed_special=()))
    assert ModelTokenizer("o200k_base").count(rendered) == expected
    assert expected != len(rendered.encode("utf-8"))
    assert expected != len(rendered)


@pytest.mark.p1
def test_rc_bud_07_hf_counts_whole_input_without_saved_truncation_or_padding(tmp_path):
    path = tmp_path / "tokenizer.json"
    independent = write_hf_tokenizer(path)
    independent.enable_truncation(max_length=2)
    independent.enable_padding(length=10)
    independent.save(str(path))
    counter = ModelTokenizer("huggingface", str(path))
    assert counter.count("hello world hello world") == 4
    assert counter.count("hello") == 1
    assert counter.count("") == 0
    assert counter.identifier.startswith("hf_json_")


@pytest.mark.p1
@pytest.mark.parametrize("fault", ["missing", "invalid_json", "invalid_model", "no_path"])
def test_rc_bud_08_missing_or_damaged_hf_config_never_returns_byte_counter(tmp_path, fault):
    path = tmp_path / "tokenizer.json"
    if fault == "invalid_json":
        path.write_text("not tokenizer json", encoding="utf-8")
    elif fault == "invalid_model":
        path.write_text('{"model": {"type": "unsupported"}}', encoding="utf-8")
    if fault == "missing":
        with pytest.raises(FileNotFoundError):
            ModelTokenizer("huggingface", str(path))
    elif fault == "no_path":
        with pytest.raises(ValueError, match="path required"):
            ModelTokenizer("huggingface")
    else:
        with pytest.raises(Exception, match=r"line 1 column \d+"):
            ModelTokenizer("huggingface", str(path))


@pytest.mark.p1
def test_rc_bud_08_unknown_encoding_is_an_explicit_configuration_failure():
    with pytest.raises(ValueError, match="Unknown encoding"):
        ModelTokenizer("not_a_real_encoding")


@pytest.mark.p1
@pytest.mark.parametrize("library", ["tiktoken", "tokenizers"])
def test_rc_bud_08_uninstalled_library_cannot_claim_tokenizer_availability(monkeypatch, library):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == library:
            raise ModuleNotFoundError("controlled missing " + library)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="controlled missing"):
        ModelTokenizer("huggingface" if library == "tokenizers" else "o200k_base", "unused.json")


@pytest.mark.p1
def test_rc_bud_08_runtime_count_failure_is_not_replaced_with_byte_length(monkeypatch):
    counter = ModelTokenizer("o200k_base")

    def failed_encode(self, text, **kwargs):
        raise RuntimeError("controlled tokenizer unavailable")

    monkeypatch.setattr(tiktoken.Encoding, "encode", failed_encode)
    with pytest.raises(RuntimeError, match="controlled tokenizer unavailable"):
        counter.count("预算不能按 UTF8 字节兜底 🙂")
