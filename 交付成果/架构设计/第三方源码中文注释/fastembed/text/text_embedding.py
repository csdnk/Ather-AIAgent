# 【中文研读】FastEmbed 的统一稠密向量入口：按模型名选择具体实现，再把 Query/Passage 请求转发下去。P3 可用它封装 Embedding Adapter；它不执行记忆授权、版本校验或 Milvus 写入。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import warnings
from typing import Any, Iterable, Sequence, Type
from dataclasses import asdict

from fastembed.common.types import NumpyArray, OnnxProvider, Device
from fastembed.text.clip_embedding import CLIPOnnxEmbedding
from fastembed.text.custom_text_embedding import CustomTextEmbedding
from fastembed.text.pooled_normalized_embedding import PooledNormalizedEmbedding
from fastembed.text.pooled_embedding import PooledEmbedding
from fastembed.text.multitask_embedding import JinaEmbeddingV3
from fastembed.text.onnx_embedding import OnnxTextEmbedding
from fastembed.text.text_embedding_base import TextEmbeddingBase
from fastembed.common.model_description import DenseModelDescription, ModelSource, PoolingType


# 【中文研读】类型职责：统一模型选择器和 Query/Passage 编码门面；具体推理由 self.model 完成。
class TextEmbedding(TextEmbeddingBase):
    EMBEDDINGS_REGISTRY: list[Type[TextEmbeddingBase]] = [
        OnnxTextEmbedding,
        CLIPOnnxEmbedding,
        PooledNormalizedEmbedding,
        PooledEmbedding,
        JinaEmbeddingV3,
        CustomTextEmbedding,
    ]

    # 【中文研读】方法职责：列出可用模型的可序列化描述
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：list[dict[str, Any]]。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def list_supported_models(cls) -> list[dict[str, Any]]:
        """Lists the supported models.

        Returns:
            list[dict[str, Any]]: A list of dictionaries containing the model information.
        """
        # 【中文研读】处理流程：从各具体实现收集模型描述，再逐条转为字典；只读注册表，不运行模型。
        return [asdict(model) for model in cls._list_supported_models()]

    # 【中文研读】方法职责：汇总所有编码实现支持的模型
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：list[DenseModelDescription]。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def _list_supported_models(cls) -> list[DenseModelDescription]:
        # 【中文研读】处理流程：依次遍历 EMBEDDINGS_REGISTRY，将每个实现的描述列表追加到一个列表；选择模型时依靠此表匹配名称。
        result: list[DenseModelDescription] = []
        for embedding in cls.EMBEDDINGS_REGISTRY:
            result.extend(embedding._list_supported_models())
        return result

    # 【中文研读】方法职责：登记新的模型及其池化和归一化约定
    # 【中文研读】输入参数：model（模型实例或模型标识）；pooling；normalization；sources；dim（向量维度）；model_file（ONNX 计算图文件名称）；description；license；size_in_gb；additional_files。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def add_custom_model(
        cls,
        model: str,
        pooling: PoolingType,
        normalization: bool,
        sources: ModelSource,
        dim: int,
        model_file: str = "onnx/model.onnx",
        description: str = "",
        license: str = "",
        size_in_gb: float = 0.0,
        additional_files: list[str] | None = None,
    ) -> None:
        # 【中文研读】处理流程：先大小写无关地检查是否重名，冲突则抛 ValueError；构建模型描述并交给 CustomTextEmbedding 注册。这里只登记能力，不能据此断言模型权重已可用。
        registered_models = cls._list_supported_models()
        for registered_model in registered_models:
            if model.lower() == registered_model.model.lower():
                raise ValueError(
                    f"Model {model} is already registered in TextEmbedding, if you still want to add this model, "
                    f"please use another model name"
                )

        CustomTextEmbedding.add_model(
            DenseModelDescription(
                model=model,
                sources=sources,
                dim=dim,
                model_file=model_file,
                description=description,
                license=license,
                size_in_GB=size_in_gb,
                additional_files=additional_files or [],
            ),
            pooling=pooling,
            normalization=normalization,
        )

    # 【中文研读】方法职责：按模型名称装配真实编码器
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；threads（单个推理会话线程数）；providers（按优先级排列的 ONNX 执行后端）；cuda（GPU 使用方式：显式启用、禁用或自动选择）；device_ids（多个 Worker 可使用的设备编号）；lazy_load（是否推迟到实际编码时加载模型）；**kwargs（透传选项）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache_dir: str | None = None,
        threads: int | None = None,
        providers: Sequence[OnnxProvider] | None = None,
        cuda: bool | Device = Device.AUTO,
        device_ids: list[int] | None = None,
        lazy_load: bool = False,
        **kwargs: Any,
    ):
        # 【中文研读】处理流程：初始化基础配置，提示已知模型版本的池化变化；查找支持该名称的实现并透传运行参数，找不到就报错。被选实现可能在构造时加载或下载模型。
        super().__init__(model_name, cache_dir, threads, **kwargs)
        if model_name.lower() == "nomic-ai/nomic-embed-text-v1.5-Q".lower():
            warnings.warn(
                "The model 'nomic-ai/nomic-embed-text-v1.5-Q' has been updated on HuggingFace. Please review "
                "the latest documentation on HF and release notes to ensure compatibility with your workflow. ",
                UserWarning,
                stacklevel=2,
            )
        if model_name.lower() in {
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2".lower(),
            "thenlper/gte-large".lower(),
            "intfloat/multilingual-e5-large".lower(),
            "sentence-transformers/paraphrase-multilingual-mpnet-base-v2".lower(),
        }:
            warnings.warn(
                f"The model {model_name} now uses mean pooling instead of CLS embedding. "
                f"In order to preserve the previous behaviour, consider either pinning fastembed version to 0.5.1 or "
                "using `add_custom_model` functionality.",
                UserWarning,
                stacklevel=2,
            )
        # 【中文研读】阶段 1：在已注册的实现中按模型名查找；同一个入口可能落到 CLS、均值池化或其他模型专用路径。
        for EMBEDDING_MODEL_TYPE in self.EMBEDDINGS_REGISTRY:
            supported_models = EMBEDDING_MODEL_TYPE._list_supported_models()
            if any(model_name.lower() == model.model.lower() for model in supported_models):
                # 【中文研读】阶段 2：把缓存、设备、线程等配置交给具体编码器；后续 embed 不再重新选择模型。
                self.model = EMBEDDING_MODEL_TYPE(
                    model_name=model_name,
                    cache_dir=cache_dir,
                    threads=threads,
                    providers=providers,
                    cuda=cuda,
                    device_ids=device_ids,
                    lazy_load=lazy_load,
                    **kwargs,
                )
                return

        raise ValueError(
            f"Model {model_name} is not supported in TextEmbedding. "
            "Please check the supported models using `TextEmbedding.list_supported_models()`"
        )

    # 【中文研读】方法职责：读取并缓存当前模型声明的向量维度
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding_size(self) -> int:
        """Get the embedding size of the current model"""
        # 【中文研读】处理流程：首次从模型描述取维度，此后复用缓存；它没有对实际输出数组做维度校验，P3 适配层仍须检查。
        if self._embedding_size is None:
            self._embedding_size = self.get_embedding_size(self.model_name)
        return self._embedding_size

    # 【中文研读】方法职责：从注册表获取指定模型的维度
    # 【中文研读】输入参数：model_name（已注册的模型标识）。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_embedding_size(cls, model_name: str) -> int:
        """Get the embedding size of the passed model

        Args:
            model_name (str): The name of the model to get embedding size for.

        Returns:
            int: The size of the embedding.

        Raises:
            ValueError: If the model name is not found in the supported models.
        """
        # 【中文研读】处理流程：大小写无关匹配模型名并读取 dim；没有匹配值则抛错，同时列出可用模型名称。
        descriptions = cls._list_supported_models()
        embedding_size: int | None = None
        for description in descriptions:
            if description.model.lower() == model_name.lower():
                embedding_size = description.dim
                break
        if embedding_size is None:
            model_names = [description.model for description in descriptions]
            raise ValueError(
                f"Embedding size for model {model_name} was None. "
                f"Available model names: {model_names}"
            )
        return embedding_size

    # 【中文研读】方法职责：把文本批次转发给已选编码器，逐条产出稠密向量
    # 【中文研读】输入参数：documents（待编码或待重排的正文集合）；batch_size（每批处理的文本或文本对数量）；parallel（数据并行进程数；None 为本进程，0 表示自动取 CPU 核数）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def embed(
        self,
        documents: str | Iterable[str],
        batch_size: int = 256,
        parallel: int | None = None,
        **kwargs: Any,
    ) -> Iterable[NumpyArray]:
        """
        Encode a list of documents into list of embeddings.
        We use mean pooling with attention so that the model can handle variable-length inputs.

        Args:
            documents: Iterator of documents or single document to embed
            batch_size: Batch size for encoding -- higher values will use more memory, but be faster
            parallel:
                If > 1, data-parallel encoding will be used, recommended for offline encoding of large datasets.
                If 0, use all available cores.
                If None, don't use data-parallel processing, use default onnxruntime threading instead.

        Returns:
            List of embeddings, one per document
        """
        # 【中文研读】处理流程：通过 yield from 委托具体实现，实际推理在迭代结果时发生。这里只统一入口，池化方式必须查看具体模型实现，不能从下面英文概述推断所有模型都用 mean pooling。
        # 【中文研读】调用方需要消费迭代器才能获得向量、触发异常；P3 记录耗时时应覆盖迭代过程，而不只是创建迭代器。
        yield from self.model.embed(documents, batch_size, parallel, **kwargs)

    # 【中文研读】方法职责：按 Query 语义调用具体编码器
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
        # 【中文研读】处理流程：保持查询角色，转交 self.model.query_embed；模型是否添加查询前缀要继续看具体实现，入口名称本身不保证已添加。
        yield from self.model.query_embed(query, **kwargs)

    # 【中文研读】方法职责：按正文语义调用具体编码器
    # 【中文研读】输入参数：texts（待编码或计数的文本）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:
        """
        Embeds a list of text passages into a list of embeddings.

        Args:
            texts (Iterable[str]): The list of texts to embed.
            **kwargs: Additional keyword argument to pass to the embed method.

        Yields:
            Iterable[SparseEmbedding]: The sparse embeddings.
        """
        # This is model-specific, so that different models can have specialized implementations
        # 【中文研读】处理流程：转交具体实现的 passage_embed 并逐条返回向量。本入口属于稠密编码；下方英文 Yields 写成 SparseEmbedding 与实际类路径不符，原文保留用于对照。
        yield from self.model.passage_embed(texts, **kwargs)

    # 【中文研读】方法职责：按当前 Embedding 模型的 tokenizer 统计输入量
    # 【中文研读】输入参数：texts（待编码或计数的文本）；batch_size（每批处理的文本或文本对数量）；**kwargs（透传选项）。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    def token_count(
        self, texts: str | Iterable[str], batch_size: int = 1024, **kwargs: Any
    ) -> int:
        """Returns the number of tokens in the texts.

        Args:
            texts (str | Iterable[str]): The list of texts to embed.
            batch_size (int): Batch size for encoding

        Returns:
            int: Sum of number of tokens in the texts.
        """
        # 【中文研读】处理流程：透传文本和批大小给模型计数器；这个数不能代替 ContextPack 下游语言模型的 token 预算。
        return self.model.token_count(texts, batch_size=batch_size, **kwargs)
