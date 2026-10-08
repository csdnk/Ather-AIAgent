"""Real, small HF tokenizer asset shared by budget boundary tests."""

from tokenizers import Tokenizer, models, pre_tokenizers


def write_hf_tokenizer(path):
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "hello": 1, "world": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(path))
    return tokenizer
