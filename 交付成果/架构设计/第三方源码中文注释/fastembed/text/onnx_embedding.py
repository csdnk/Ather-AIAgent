# 【中文研读】ONNX 稠密编码的一种具体实现：解析模型文件→按批执行→取句向量→归一化。模型列表属于上游支持清单，不代表 P3 已选用全部模型。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
from typing import Any, Iterable, Sequence, Type

from fastembed.common.types import NumpyArray, OnnxProvider, Device
from fastembed.common.onnx_model import OnnxOutputContext
from fastembed.common.utils import define_cache_dir, normalize
from fastembed.text.onnx_text_model import OnnxTextModel, TextEmbeddingWorker
from fastembed.text.text_embedding_base import TextEmbeddingBase
from fastembed.common.model_description import DenseModelDescription, ModelSource

supported_onnx_models: list[DenseModelDescription] = [
    DenseModelDescription(
        model="BAAI/bge-base-en",
        dim=768,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2023 year."
        ),
        license="mit",
        size_in_GB=0.42,
        sources=ModelSource(
            hf="Qdrant/fast-bge-base-en",
            url="https://storage.googleapis.com/qdrant-fastembed/fast-bge-base-en.tar.gz",
            _deprecated_tar_struct=True,
        ),
        model_file="model_optimized.onnx",
    ),
    DenseModelDescription(
        model="BAAI/bge-base-en-v1.5",
        dim=768,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: not so necessary, 2023 year."
        ),
        license="mit",
        size_in_GB=0.21,
        sources=ModelSource(
            hf="qdrant/bge-base-en-v1.5-onnx-q",
            url="https://storage.googleapis.com/qdrant-fastembed/fast-bge-base-en-v1.5.tar.gz",
            _deprecated_tar_struct=True,
        ),
        model_file="model_optimized.onnx",
    ),
    DenseModelDescription(
        model="BAAI/bge-large-en-v1.5",
        dim=1024,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: not so necessary, 2023 year."
        ),
        license="mit",
        size_in_GB=1.20,
        sources=ModelSource(hf="qdrant/bge-large-en-v1.5-onnx"),
        model_file="model.onnx",
    ),
    DenseModelDescription(
        model="BAAI/bge-small-en",
        dim=384,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2023 year."
        ),
        license="mit",
        size_in_GB=0.13,
        sources=ModelSource(
            hf="Qdrant/bge-small-en",
            url="https://storage.googleapis.com/qdrant-fastembed/BAAI-bge-small-en.tar.gz",
            _deprecated_tar_struct=True,
        ),
        model_file="model_optimized.onnx",
    ),
    DenseModelDescription(
        model="BAAI/bge-small-en-v1.5",
        dim=384,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: not so necessary, 2023 year."
        ),
        license="mit",
        size_in_GB=0.067,
        sources=ModelSource(hf="qdrant/bge-small-en-v1.5-onnx-q"),
        model_file="model_optimized.onnx",
    ),
    DenseModelDescription(
        model="BAAI/bge-small-zh-v1.5",
        dim=512,
        description=(
            "Text embeddings, Unimodal (text), Chinese, 512 input tokens truncation, "
            "Prefixes for queries/documents: not so necessary, 2023 year."
        ),
        license="mit",
        size_in_GB=0.09,
        sources=ModelSource(
            hf="Qdrant/bge-small-zh-v1.5",
            url="https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz",
            _deprecated_tar_struct=True,
        ),
        model_file="model_optimized.onnx",
    ),
    DenseModelDescription(
        model="mixedbread-ai/mxbai-embed-large-v1",
        dim=1024,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2024 year."
        ),
        license="apache-2.0",
        size_in_GB=0.64,
        sources=ModelSource(hf="mixedbread-ai/mxbai-embed-large-v1"),
        model_file="onnx/model.onnx",
    ),
    DenseModelDescription(
        model="snowflake/snowflake-arctic-embed-xs",
        dim=384,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2024 year."
        ),
        license="apache-2.0",
        size_in_GB=0.09,
        sources=ModelSource(hf="snowflake/snowflake-arctic-embed-xs"),
        model_file="onnx/model.onnx",
    ),
    DenseModelDescription(
        model="snowflake/snowflake-arctic-embed-s",
        dim=384,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2024 year."
        ),
        license="apache-2.0",
        size_in_GB=0.13,
        sources=ModelSource(hf="snowflake/snowflake-arctic-embed-s"),
        model_file="onnx/model.onnx",
    ),
    DenseModelDescription(
        model="snowflake/snowflake-arctic-embed-m",
        dim=768,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2024 year."
        ),
        license="apache-2.0",
        size_in_GB=0.43,
        sources=ModelSource(hf="Snowflake/snowflake-arctic-embed-m"),
        model_file="onnx/model.onnx",
    ),
    DenseModelDescription(
        model="snowflake/snowflake-arctic-embed-m-long",
        dim=768,
        description=(
            "Text embeddings, Unimodal (text), English, 2048 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2024 year."
        ),
        license="apache-2.0",
        size_in_GB=0.54,
        sources=ModelSource(hf="snowflake/snowflake-arctic-embed-m-long"),
        model_file="onnx/model.onnx",
    ),
    DenseModelDescription(
        model="snowflake/snowflake-arctic-embed-l",
        dim=1024,
        description=(
            "Text embeddings, Unimodal (text), English, 512 input tokens truncation, "
            "Prefixes for queries/documents: necessary, 2024 year."
        ),
        license="apache-2.0",
        size_in_GB=1.02,
        sources=ModelSource(hf="snowflake/snowflake-arctic-embed-l"),
        model_file="onnx/model.onnx",
    ),
    DenseModelDescription(
        model="jinaai/jina-clip-v1",
        dim=768,
        description=(
            "Text embeddings, Multimodal (text&image), English, Prefixes for queries/documents: "
            "not necessary, 2024 year"
        ),
        license="apache-2.0",
        size_in_GB=0.55,
        sources=ModelSource(hf="jinaai/jina-clip-v1"),
        model_file="onnx/text_model.onnx",
    ),
]


# 【中文研读】类型职责：具体 ONNX 稠密模型实现，组合文本批处理、模型文件管理和输出转换。
class OnnxTextEmbedding(TextEmbeddingBase, OnnxTextModel[NumpyArray]):
    """Implementation of the Flag Embedding model."""

    # 【中文研读】方法职责：返回此实现的模型描述清单
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：list[DenseModelDescription]。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def _list_supported_models(cls) -> list[DenseModelDescription]:
        """
        Lists the supported models.

        Returns:
            list[DenseModelDescription]: A list of DenseModelDescription objects containing the model information.
        """
        # 【中文研读】处理流程：这里只提供选择依据，实际加载由构造器与 load_onnx_model 完成。
        return supported_onnx_models

    # 【中文研读】方法职责：确定模型文件、设备与加载时机
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；threads（单个推理会话线程数）；providers（按优先级排列的 ONNX 执行后端）；cuda（GPU 使用方式：显式启用、禁用或自动选择）；device_ids（多个 Worker 可使用的设备编号）；lazy_load（是否推迟到实际编码时加载模型）；device_id（当前进程使用的设备编号）；specific_model_path（显式提供的模型文件目录）；**kwargs（透传选项）。
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
        device_id: int | None = None,
        specific_model_path: str | None = None,
        **kwargs: Any,
    ):
        """
        Args:
            model_name (str): The name of the model to use.
            cache_dir (str, optional): The path to the cache directory.
                                       Can be set using the `FASTEMBED_CACHE_PATH` env variable.
                                       Defaults to `fastembed_cache` in the system's temp directory.
            threads (int, optional): The number of threads single onnxruntime session can use. Defaults to None.
            providers (Optional[Sequence[OnnxProvider]], optional): The list of onnxruntime providers to use.
                Mutually exclusive with the `cuda` and `device_ids` arguments. Defaults to None.
            cuda (Union[bool, Device], optional): Whether to use cuda for inference. Mutually exclusive with `providers`
                Defaults to Device.AUTO.
            device_ids (Optional[list[int]], optional): The list of device ids to use for data parallel processing in
                workers. Should be used with `cuda` equals to `True`, `Device.AUTO` or `Device.CUDA`, mutually exclusive
                with `providers`. Defaults to None.
            lazy_load (bool, optional): Whether to load the model during class initialization or on demand.
                Should be set to True when using multiple-gpu and parallel encoding. Defaults to False.
            device_id (Optional[int], optional): The device id to use for loading the model in the worker process.
            specific_model_path (Optional[str], optional): The specific path to the onnx model dir if it should be imported from somewhere else

        Raises:
            ValueError: If the model_name is not in the format <org>/<model> e.g. BAAI/bge-base-en.
        """
        # 【中文研读】处理流程：保存后端和选项，选定设备编号，读取模型描述并解析缓存路径；取得本地或下载的模型目录，除非 lazy_load 开启，否则立即加载 ONNX 会话。
        super().__init__(model_name, cache_dir, threads, **kwargs)
        self.providers = providers
        self.lazy_load = lazy_load
        self._extra_session_options = self._select_exposed_session_options(kwargs)
        # List of device ids, that can be used for data parallel processing in workers
        self.device_ids = device_ids
        self.cuda = cuda

        # This device_id will be used if we need to load model in current process
        self.device_id: int | None = None
        if device_id is not None:
            self.device_id = device_id
        elif self.device_ids is not None:
            self.device_id = self.device_ids[0]

        self.model_description = self._get_model_description(model_name)
        self.cache_dir = str(define_cache_dir(cache_dir))
        self._specific_model_path = specific_model_path
        # 【中文研读】阶段 1：定位权重和 tokenizer。这里可能涉及磁盘与网络，下载完成也不等于推理已通过；P3 的就绪检测需要另外执行受控探测。
        self._model_dir = self.download_model(
            self.model_description,
            self.cache_dir,
            local_files_only=self._local_files_only,
            specific_model_path=self._specific_model_path,
        )

        # 【中文研读】阶段 2：决定现在加载还是首次消费编码迭代器时加载；这会影响冷启动延迟与错误发生位置。
        if not self.lazy_load:
            self.load_onnx_model()

    # 【中文研读】方法职责：把编码请求接入通用分批执行流程
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
        # 【中文研读】处理流程：传入已绑定的模型、设备、文件来源和会话选项，由 _embed_documents 选择本进程或进程池执行，结果以生成器产出。
        yield from self._embed_documents(
            model_name=self.model_name,
            cache_dir=str(self.cache_dir),
            documents=documents,
            batch_size=batch_size,
            parallel=parallel,
            providers=self.providers,
            cuda=self.cuda,
            device_ids=self.device_ids,
            local_files_only=self._local_files_only,
            specific_model_path=self._specific_model_path,
            extra_session_options=self._extra_session_options,
            **kwargs,
        )

    # 【中文研读】方法职责：指定进程池启动的具体编码 Worker
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Type['TextEmbeddingWorker[NumpyArray]']。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def _get_worker_class(cls) -> Type["TextEmbeddingWorker[NumpyArray]"]:
        # 【中文研读】处理流程：返回类对象，尚未创建进程；后续池负责实例化。
        return OnnxTextEmbeddingWorker

    # 【中文研读】方法职责：保留已经组装好的 ONNX 输入
    # 【中文研读】输入参数：onnx_input（以图输入名称为键、张量为值的字典）；**kwargs（透传选项）。
    # 【中文研读】返回约定：dict[str, NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def _preprocess_onnx_input(
        self, onnx_input: dict[str, NumpyArray], **kwargs: Any
    ) -> dict[str, NumpyArray]:
        """
        Preprocess the onnx input.
        """
        # 【中文研读】处理流程：此实现原样返回字典；其他模型可以覆盖此钩子，不能推定所有模型输入完全相同。
        return onnx_input

    # 【中文研读】方法职责：把图输出转换为可检索句向量
    # 【中文研读】输入参数：output（推理输出及其输入掩码）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def _post_process_onnx_output(
        self, output: OnnxOutputContext, **kwargs: Any
    ) -> Iterable[NumpyArray]:
        # 【中文研读】处理流程：三维输出取每条文本第一个 token，二维输出直接使用，其他形状报错；最后归一化。实际代码为本实现的依据，并非通用 mean pooling。
        embeddings = output.model_output

        # 【中文研读】例如形状为 [2,128,768]：两条文本、每条 128 个位置、每位置 768 维；此分支取 [:,0]，得到 [2,768]。
        if embeddings.ndim == 3:  # (batch_size, seq_len, embedding_dim)
            processed_embeddings = embeddings[:, 0]
        # 【中文研读】图本身已经输出每条文本一个向量时，不再做 token 级池化。
        elif embeddings.ndim == 2:  # (batch_size, embedding_dim)
            processed_embeddings = embeddings
        else:
            raise ValueError(f"Unsupported embedding shape: {embeddings.shape}")
        # 【中文研读】阶段 3：统一向量尺度。P3 还需绑定模型名称、版本、维度和预处理约定，不能把同维度的不同模型空间混用。
        return normalize(processed_embeddings)

    # 【中文研读】方法职责：用绑定的文件与设备创建推理会话
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def load_onnx_model(self) -> None:
        # 【中文研读】处理流程：将模型目录、图文件名、线程和后端传给继承的加载器；它还会加载同一目录下的 tokenizer。
        self._load_onnx_model(
            model_dir=self._model_dir,
            model_file=self.model_description.model_file,
            threads=self.threads,
            providers=self.providers,
            cuda=self.cuda,
            device_id=self.device_id,
            extra_session_options=self._extra_session_options,
        )

    # 【中文研读】方法职责：调用共用 tokenizer 计数实现
    # 【中文研读】输入参数：texts（待编码或计数的文本）；batch_size（每批处理的文本或文本对数量）；**kwargs（透传选项）。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    def token_count(
        self, texts: str | Iterable[str], batch_size: int = 1024, **kwargs: Any
    ) -> int:
        # 【中文研读】处理流程：透传文本及批大小，结果是 Embedding 分词口径的总 token 数。
        return self._token_count(texts, batch_size=batch_size, **kwargs)


# 【中文研读】类型职责：进程池中的模型工厂；每个 Worker 持有自己的编码实例。
class OnnxTextEmbeddingWorker(TextEmbeddingWorker[NumpyArray]):
    # 【中文研读】方法职责：在子进程中创建编码器
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；**kwargs（透传选项）。
    # 【中文研读】返回约定：OnnxTextEmbedding。结果的业务含义与失败分支见下面处理流程。
    def init_embedding(
        self,
        model_name: str,
        cache_dir: str,
        **kwargs: Any,
    ) -> OnnxTextEmbedding:
        # 【中文研读】处理流程：固定该会话 threads=1，再传入模型路径等参数，避免每个 Worker 同时启用大量会话线程。
        return OnnxTextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=1,
            **kwargs,
        )
