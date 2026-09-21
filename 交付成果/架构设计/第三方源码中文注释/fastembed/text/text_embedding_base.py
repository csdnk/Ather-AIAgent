# 【中文研读】Query 与 Passage 的默认分流契约。此基类两条路径最终均调用 embed，并未自动拼任何指令；需要前缀的模型由专用实现或 P3 已明确的适配约定负责。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
from typing import Iterable, Any

from fastembed.common.model_description import DenseModelDescription
from fastembed.common.types import NumpyArray
from fastembed.common.model_management import ModelManagement


# 【中文研读】类型职责：稠密文本编码的公共接口和默认 Query/Passage 转发规则。
class TextEmbeddingBase(ModelManagement[DenseModelDescription]):
    # 【中文研读】方法职责：保存通用模型配置
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；threads（单个推理会话线程数）；**kwargs（透传选项）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        model_name: str,
        cache_dir: str | None = None,
        threads: int | None = None,
        **kwargs: Any,
    ):
        # 【中文研读】处理流程：记录名称、缓存和线程选项，取出 local_files_only 标记并清空维度缓存；没有在这里加载模型。
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threads = threads
        self._local_files_only = kwargs.pop("local_files_only", False)
        self._embedding_size: int | None = None

    # 【中文研读】方法职责：要求子类提供真实文本编码
    # 【中文研读】输入参数：documents（待编码或待重排的正文集合）；batch_size（每批处理的文本或文本对数量）；parallel（数据并行进程数；None 为本进程，0 表示自动取 CPU 核数）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def embed(
        self,
        documents: str | Iterable[str],
        batch_size: int = 256,
        parallel: int | None = None,
        **kwargs: Any,
    ) -> Iterable[NumpyArray]:
        # 【中文研读】处理流程：基类直接抛 NotImplementedError，不能实例化后当作已可用编码服务。
        raise NotImplementedError()

    # 【中文研读】方法职责：默认将正文原样交给 embed
    # 【中文研读】输入参数：texts（待编码或计数的文本）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:
        """
        Embeds a list of text passages into a list of embeddings.

        Args:
            texts (Iterable[str]): The list of texts to embed.
            **kwargs: Additional keyword argument to pass to the embed method.

        Yields:
            Iterable[NumpyArray]: The embeddings.
        """

        # This is model-specific, so that different models can have specialized implementations
        # 【中文研读】处理流程：不添加前缀、不查询向量库，通过生成器逐条传回子类产物。
        # 【中文研读】正文与查询都生成稠密向量；这两个角色入口不意味着采用两种索引，也不意味着一定使用两套模型。
        yield from self.embed(texts, **kwargs)

    # 【中文研读】方法职责：统一单条查询和多条查询的输入形状
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def query_embed(self, query: str | Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:
        """
        Embeds queries

        Args:
            query (Union[str, Iterable[str]]): The query to embed, or an iterable e.g. list of queries.

        Returns:
            Iterable[NumpyArray]: The embeddings.
        """

        # This is model-specific, so that different models can have specialized implementations
        # 【中文研读】处理流程：单字符串包成列表，其他可迭代输入直接透传；两条分支都交给 embed，不执行关键词查询。
        # 【中文研读】例如 query="我喜欢什么饮料" 时，先转成只有一个元素的批次；不会把字符串当成逐字符文本流。
        if isinstance(query, str):
            yield from self.embed([query], **kwargs)
        else:
            yield from self.embed(query, **kwargs)

    # 【中文研读】方法职责：要求子类给出指定模型维度
    # 【中文研读】输入参数：model_name（已注册的模型标识）。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_embedding_size(cls, model_name: str) -> int:
        """Returns embedding size of the passed model."""
        # 【中文研读】处理流程：默认抛 NotImplementedError；维度关系到向量集合兼容性，不能猜测。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：声明实例级维度读取接口
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding_size(self) -> int:
        """Returns embedding size for the current model"""
        # 【中文研读】处理流程：默认抛 NotImplementedError，具体实现负责从模型描述获取。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：声明 Embedding tokenizer 计数接口
    # 【中文研读】输入参数：texts（待编码或计数的文本）；**kwargs（透传选项）。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    def token_count(self, texts: str | Iterable[str], **kwargs: Any) -> int:
        """Returns the number of tokens in the texts."""
        # 【中文研读】处理流程：默认抛 NotImplementedError；子类提供实现后才可用于输入截断判断。
        raise NotImplementedError("Subclasses must implement this method")
