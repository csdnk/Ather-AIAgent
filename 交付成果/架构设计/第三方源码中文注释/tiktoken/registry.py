# 【中文研读】固定 tokenizer 名称的解析入口。P3 应记录编码名称和版本，以相同口径计算 ContextPack；注册器缓存的是编码器对象，不是业务结果。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
from __future__ import annotations

import functools
import importlib
import pkgutil
import threading
from typing import Any, Callable, Sequence

import tiktoken_ext

import tiktoken
from tiktoken.core import Encoding

_lock = threading.RLock()
ENCODINGS: dict[str, Encoding] = {}
ENCODING_CONSTRUCTORS: dict[str, Callable[[], dict[str, Any]]] | None = None


# 【中文研读】方法职责：列出可提供编码表的插件模块
# 【中文研读】输入参数：使用对象已有状态。
# 【中文研读】返回约定：Sequence[str]。结果的业务含义与失败分支见下面处理流程。
@functools.lru_cache
def _available_plugin_modules() -> Sequence[str]:
    # tiktoken_ext is a namespace package
    # submodules inside tiktoken_ext will be inspected for ENCODING_CONSTRUCTORS attributes
    # - we use namespace package pattern so `pkgutil.iter_modules` is fast
    # - it's a separate top-level package because namespace subpackages of non-namespace
    #   packages don't quite do what you want with editable installs
    # 【中文研读】处理流程：扫描 tiktoken_ext 命名空间，缓存模块名称列表；尚未把某段文本编码。
    mods = []
    plugin_mods = pkgutil.iter_modules(tiktoken_ext.__path__, tiktoken_ext.__name__ + ".")
    for _, mod_name, _ in plugin_mods:
        mods.append(mod_name)
    return mods


# 【中文研读】方法职责：注册编码名称对应的构造函数
# 【中文研读】输入参数：使用对象已有状态。
# 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
def _find_constructors() -> None:
    # 【中文研读】处理流程：持可重入锁检查初始化状态，加载插件，拒绝缺失导出和重复名称；任一异常都清空半成品状态后重新抛出。
    global ENCODING_CONSTRUCTORS
    # 【中文研读】进程内锁保护共享注册表，避免并发初始化相互覆盖；不是跨服务事务，也不是分布式任务锁。
    with _lock:
        if ENCODING_CONSTRUCTORS is not None:
            return
        ENCODING_CONSTRUCTORS = {}

        try:
            for mod_name in _available_plugin_modules():
                mod = importlib.import_module(mod_name)
                try:
                    constructors = mod.ENCODING_CONSTRUCTORS
                except AttributeError as e:
                    raise ValueError(
                        f"tiktoken plugin {mod_name} does not define ENCODING_CONSTRUCTORS"
                    ) from e
                for enc_name, constructor in constructors.items():
                    if enc_name in ENCODING_CONSTRUCTORS:
                        raise ValueError(
                            f"Duplicate encoding name {enc_name} in tiktoken plugin {mod_name}"
                        )
                    ENCODING_CONSTRUCTORS[enc_name] = constructor
        except Exception:
            # Ensure we idempotently raise errors
            # 【中文研读】注册失败恢复到未初始化状态并保留异常，避免其他调用使用不完整的构造器表。
            ENCODING_CONSTRUCTORS = None
            raise




# 【中文研读】方法职责：根据明确名称获取或创建 Encoding
# 【中文研读】输入参数：encoding_name（固定编码表名称，例如 o200k_base）。
# 【中文研读】返回约定：Encoding。结果的业务含义与失败分支见下面处理流程。
def get_encoding(encoding_name: str) -> Encoding:
    # 【中文研读】处理流程：校验字符串，先查缓存，再持锁二次检查；必要时加载构造器，未知编码报错，成功构造后缓存返回。
    if not isinstance(encoding_name, str):
        raise ValueError(f"Expected a string in get_encoding, got {type(encoding_name)}")

    if encoding_name in ENCODINGS:
        return ENCODINGS[encoding_name]

    # 【中文研读】进程内锁保护共享注册表，避免并发初始化相互覆盖；不是跨服务事务，也不是分布式任务锁。
    with _lock:
        if encoding_name in ENCODINGS:
            return ENCODINGS[encoding_name]

        if ENCODING_CONSTRUCTORS is None:
            _find_constructors()
            assert ENCODING_CONSTRUCTORS is not None

        if encoding_name not in ENCODING_CONSTRUCTORS:
            raise ValueError(
                f"Unknown encoding {encoding_name}.\n"
                f"Plugins found: {_available_plugin_modules()}\n"
                f"tiktoken version: {tiktoken.__version__} (are you on latest?)"
            )

        constructor = ENCODING_CONSTRUCTORS[encoding_name]
        # 【中文研读】构造器提供词表、特殊 token 与正则，再创建编码器；首次调用可能涉及编码资源读取，不能假定零成本。
        enc = Encoding(**constructor())
        ENCODINGS[encoding_name] = enc
        return enc


# 【中文研读】方法职责：列出已发现的可用编码名称
# 【中文研读】输入参数：使用对象已有状态。
# 【中文研读】返回约定：list[str]。结果的业务含义与失败分支见下面处理流程。
def list_encoding_names() -> list[str]:
    # 【中文研读】处理流程：持锁确保构造器已加载，再返回名称列表；这不表示每个编码表都已加载进内存。
    # 【中文研读】进程内锁保护共享注册表，避免并发初始化相互覆盖；不是跨服务事务，也不是分布式任务锁。
    with _lock:
        if ENCODING_CONSTRUCTORS is None:
            _find_constructors()
            assert ENCODING_CONSTRUCTORS is not None
        return list(ENCODING_CONSTRUCTORS)
