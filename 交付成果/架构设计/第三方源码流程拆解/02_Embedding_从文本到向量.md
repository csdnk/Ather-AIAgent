# Embedding：从一段文本到一个向量

[返回流程总入口](README.md)

本章先看启动时怎样加载模型，再跟随一次查询编码进入不同文件。假定模型选择落在 OnnxTextEmbedding，采用单进程默认路径；FastEmbed 其他模型实现可能使用不同池化或前缀，不应由本示例推断全部模型行为。

## 先看这一条执行路径

```text
启动：TextEmbedding.__init__ → TextEmbeddingBase.__init__
  → OnnxTextEmbedding.__init__ → 解析模型文件
  → load_onnx_model → OnnxTextModel._load_onnx_model
  → OnnxModel._load_onnx_model → ORT InferenceSession → 配套 tokenizer
请求：TextEmbedding.query_embed → 具体实现的 query_embed（此路径继承基类）
  → OnnxTextEmbedding.embed → _embed_documents
  → onnx_embed → tokenize → 构造输入张量 → Session.run
  → 返回原始图输出 → _post_process_onnx_output → 返回稠密向量
正文：passage_embed → embed → 同一模型空间
P3：核对输入数量、ID 绑定、维度、数值和模型空间
```

贯穿示例：Query 为“这个项目预算是多少”，Passage 为“项目预算最终批准为十五万元”。它们各自编码得到向量，再由检索流程比较。这里没有关键词索引，也没有两个依次理解文本的大模型。示意形状 [1,128,768] 仅用于解释维度，不代表项目固定模型配置。

以下代码从已核对版本逐段摘录；省略导入、英文说明文档及英文整行注释，保留执行语句、字符串与中文研读注释。标为“片段”的代码保留原方法的局部上下文，不能当成独立函数运行。

## 按执行顺序展开

- [步骤 01：启动：由统一入口选中具体模型实现](#s01)
- [步骤 02：展开：保存公共设置](#s02)
- [步骤 03：启动：找到图文件并确定何时加载](#s03)
- [步骤 04：把模型文件配置交给通用加载器](#s04)
- [步骤 05：先加载图，再加载配套 tokenizer](#s05)
- [步骤 06：选择后端与线程，建立运行时会话](#s06)
- [步骤 07：展开：ORT 解析文件或字节并尝试创建](#s07)
- [步骤 08：展开：底层加载、初始化并缓存图元信息](#s08)
- [步骤 09：请求进入：Query 统一入口](#s09)
- [步骤 10：展开：Query 默认实现规范化批次](#s10)
- [步骤 11：进入具体编码器：附上实例配置](#s11)
- [步骤 12：按批次执行；小输入留在当前进程](#s12)
- [步骤 13：准备一次 ONNX 推理：先分词](#s13)
- [步骤 14：展开：tokenizer 批量编码](#s14)
- [步骤 15：回到推理：按图声明组装输入](#s15)
- [步骤 16：展开：这个模型的输入钩子原样返回](#s16)
- [步骤 17：实际推理：校验后进入 C++，必要时有限回退](#s17)
- [步骤 18：返回上层：保留原始输出与掩码](#s18)
- [步骤 19：把原始输出变成最终句向量](#s19)
- [步骤 20：正文入口的区别：保持 Passage 角色](#s20)
- [步骤 21：正文默认路径仍调用同一个 embed](#s21)

<a id="s01"></a>

### 01　启动：由统一入口选中具体模型实现

**当前执行位置：** `TextEmbedding.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/text_embedding.py)，注释版第 97—150 行。

**收到什么：** 模型名、缓存、设备、线程和 lazy_load。

**这一段怎么处理：** 先保存公共设置，再按注册表找到能处理模型名的类，并构造 self.model。

```python
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
```

**执行后得到什么：** 具体编码器实例。

**接下来到哪里：** 先展开步骤 02 的基础设置，再进入步骤 03 的 ONNX 构造器。

**失败与 P3 责任：** 不同模型落到不同类，不能因为入口相同就认为池化与预处理相同。

<a id="s02"></a>

### 02　展开：保存公共设置

**当前执行位置：** `TextEmbeddingBase.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/text_embedding_base.py)，注释版第 15—27 行。

**收到什么：** 同一套模型设置。

**这一段怎么处理：** 记录模型名、缓存和线程，解析 local_files_only，初始化维度缓存。

```python
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
```

**执行后得到什么：** 基础实例状态。

**接下来到哪里：** 返回上层构造器，继续选择和创建 ONNX 编码器。

**失败与 P3 责任：** 设置 local_files_only 并不验证模型文件完整或可用。

<a id="s03"></a>

### 03　启动：找到图文件并确定何时加载

**当前执行位置：** `OnnxTextEmbedding.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_embedding.py)，注释版第 209—273 行。

**收到什么：** 模型描述、设备参数与加载配置。

**这一段怎么处理：** 决定当前设备，取得模型目录；若未启用懒加载，立即加载会话。

```python
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
    # 【中文研读】处理流程：保存后端和选项，选定设备编号，读取模型描述并解析缓存路径；取得本地或下载的模型目录，除非 lazy_load 开启，否则立即加载 ONNX 会话。
    super().__init__(model_name, cache_dir, threads, **kwargs)
    self.providers = providers
    self.lazy_load = lazy_load
    self._extra_session_options = self._select_exposed_session_options(kwargs)
    self.device_ids = device_ids
    self.cuda = cuda

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
```

**执行后得到什么：** 已绑定的目录、描述与可能已经建立的模型会话。

**接下来到哪里：** 步骤 04 展开 load_onnx_model；懒加载时推迟到首次实际编码。

**失败与 P3 责任：** 下载/磁盘/依赖错误可能在构造发生，也可能在首次消费向量时发生；健康检测要区分。

<a id="s04"></a>

### 04　把模型文件配置交给通用加载器

**当前执行位置：** `OnnxTextEmbedding.load_onnx_model`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_embedding.py)，注释版第 359—369 行。

**收到什么：** 实例保存的模型目录、图文件名、线程和设备。

**这一段怎么处理：** 把无参加载入口转成带明确文件参数的 _load_onnx_model。

```python
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
```

**执行后得到什么：** 即将执行的加载请求。

**接下来到哪里：** 进入步骤 05，注意此处是继承解析，不是在同一文件里完成。

**失败与 P3 责任：** 未执行文本；加载成功不等于一次向量请求成功。

<a id="s05"></a>

### 05　先加载图，再加载配套 tokenizer

**当前执行位置：** `OnnxTextModel._load_onnx_model`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_text_model.py)，注释版第 71—91 行。

**收到什么：** 模型目录和会话参数。

**这一段怎么处理：** 先调用父类图加载，再从同一目录读取 tokenizer 与特殊 token 映射。

```python
def _load_onnx_model(
    self,
    model_dir: Path,
    model_file: str,
    threads: int | None,
    providers: Sequence[OnnxProvider] | None = None,
    cuda: bool | Device = Device.AUTO,
    device_id: int | None = None,
    extra_session_options: dict[str, Any] | None = None,
) -> None:
    # 【中文研读】处理流程：先调用基类创建 ONNX 会话，再从相同模型目录读取 tokenizer 与特殊 token；缺失或不兼容会在加载或编码时失败。
    super()._load_onnx_model(
        model_dir=model_dir,
        model_file=model_file,
        threads=threads,
        providers=providers,
        cuda=cuda,
        device_id=device_id,
        extra_session_options=extra_session_options,
    )
    self.tokenizer, self.special_token_to_id = load_tokenizer(model_dir=model_dir)
```

**执行后得到什么：** self.model 与 self.tokenizer。

**接下来到哪里：** 父类加载具体展开在步骤 06；完成后返回此处加载 tokenizer。

**失败与 P3 责任：** 图和分词配置必须配套，不能随意换一个 tokenizer。

<a id="s06"></a>

### 06　选择后端与线程，建立运行时会话

**当前执行位置：** `OnnxModel._load_onnx_model`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/common/onnx_model.py)，注释版第 81—155 行。

**收到什么：** ONNX 模型路径、CPU/GPU 与会话设置。

**这一段怎么处理：** 检查 provider 可用性，显式配置优先；启用图优化和线程设置，再创建 InferenceSession，并检查实际后端。

```python
def _load_onnx_model(
    self,
    model_dir: Path,
    model_file: str,
    threads: int | None,
    providers: Sequence[OnnxProvider] | None = None,
    cuda: bool | Device = Device.AUTO,
    device_id: int | None = None,
    extra_session_options: dict[str, Any] | None = None,
) -> None:
    # 【中文研读】处理流程：拼接模型路径，检测可用后端，按显式 providers 或设备设置选择后端；校验可用性，设置图优化与线程，再创建会话，检查期望 CUDA 是否实际启用。
    model_path = model_dir / model_file
    # 【中文研读】阶段 1：检查当前安装的运行时能提供哪些计算后端；有 GPU 硬件不代表已安装对应 provider。
    available_providers = ort.get_available_providers()
    cuda_available = "CUDAExecutionProvider" in available_providers
    explicit_cuda = cuda is True or cuda == Device.CUDA

    if explicit_cuda and providers is not None:
        warnings.warn(
            f"`cuda` and `providers` are mutually exclusive parameters, "
            f"cuda: {cuda}, providers: {providers}. If you'd like to use providers, cuda should be one of "
            f"[False, Device.CPU, Device.AUTO].",
            category=UserWarning,
            stacklevel=6,
        )

    # 【中文研读】显式后端配置优先；否则才依据 cuda 选择。配置冲突会产生警告，不应把警告当成目标设备已生效的证明。
    if providers is not None:
        onnx_providers = list(providers)
    elif explicit_cuda or (cuda == Device.AUTO and cuda_available):
        if device_id is None:
            onnx_providers = ["CUDAExecutionProvider"]
        else:
            onnx_providers = [("CUDAExecutionProvider", {"device_id": device_id})]
    else:
        onnx_providers = ["CPUExecutionProvider"]

    requested_provider_names: list[str] = []
    for provider in onnx_providers:
        provider_name = provider if isinstance(provider, str) else provider[0]
        requested_provider_names.append(provider_name)
        # 【中文研读】请求了不可用后端就明确失败；这里不会悄悄生成替代向量。
        if provider_name not in available_providers:
            raise ValueError(
                f"Provider {provider_name} is not available. Available providers: {available_providers}"
            )

    # 【中文研读】阶段 2：设置图优化与线程。多个 P3 Worker 各开大量线程会争抢资源，应由部署配置统一预算。
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    if threads is not None:
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = threads

    if extra_session_options is not None:
        self.add_extra_session_options(so, extra_session_options)

    # 【中文研读】阶段 3：加载计算图并建立会话；此处不运行某条正文，也不向 Milvus 写向量。
    self.model = ort.InferenceSession(
        str(model_path), providers=onnx_providers, sess_options=so
    )
    if "CUDAExecutionProvider" in requested_provider_names:
        assert self.model is not None
        # 【中文研读】阶段 4：核对实际后端。运行时可能发生回退，记录配置值不足以证明在 GPU 上运行。
        current_providers = self.model.get_providers()
        if "CUDAExecutionProvider" not in current_providers:
            warnings.warn(
                f"Attempt to set CUDAExecutionProvider failed. Current providers: {current_providers}."
                "If you are using CUDA 12.x, install onnxruntime-gpu via "
                "`pip install onnxruntime-gpu --extra-index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/`",
                RuntimeWarning,
            )
```

**执行后得到什么：** 可供重复调用的推理会话。

**接下来到哪里：** 创建动作进入步骤 07；完成后返回上层加载 tokenizer。

**失败与 P3 责任：** GPU 请求可能失败或回退；P3 应记录实际后端，不能只记录希望使用 GPU。

<a id="s07"></a>

### 07　展开：ORT 解析文件或字节并尝试创建

**当前执行位置：** `InferenceSession.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/onnxruntime/capi/onnxruntime_inference_collection.py)，注释版第 684—764 行。

**收到什么：** 模型文件路径/字节及后端配置。

**这一段怎么处理：** 区分输入类型，保存会话选项，尝试创建底层会话；特定错误下允许一次后备后端尝试。

```python
def __init__(
    self,
    path_or_bytes: str | bytes | os.PathLike,
    sess_options: C.SessionOptions | None = None,
    providers: Sequence[str | tuple[str, dict[Any, Any]]] | None = None,
    provider_options: Sequence[dict[Any, Any]] | None = None,
    **kwargs,
) -> None:
    # 【中文研读】处理流程：校验输入类型，保存选项，调用 _create_inference_session；特定初始化错误在允许时尝试后备后端一次，再失败保留异常链。
    super().__init__(enable_fallback=int(kwargs.get("enable_fallback", 1)) == 1)

    # 【中文研读】加载阶段 1：区分文件路径与模型字节；前者记录路径，后者持有完整字节。其他类型立即拒绝，不进入模型执行。
    if isinstance(path_or_bytes, (str, os.PathLike)):
        self._model_path = os.fspath(path_or_bytes)
        self._model_bytes = None
    elif isinstance(path_or_bytes, bytes):
        self._model_path = None
        self._model_bytes = path_or_bytes  # TODO: This is bad as we're holding the memory indefinitely
    else:
        raise TypeError(f"Unable to load from type '{type(path_or_bytes)}'")

    self._sess_options = sess_options
    # 【中文研读】保存原始会话选项，后续切换执行后端、重建会话时仍以此作为配置来源。
    self._sess_options_initial = sess_options
    if "read_config_from_model" in kwargs:
        self._read_config_from_model = int(kwargs["read_config_from_model"]) == 1
    else:
        self._read_config_from_model = os.environ.get("ORT_LOAD_CONFIG_FROM_MODEL") == "1"

    disabled_optimizers = kwargs.get("disabled_optimizers")

    try:
        self._create_inference_session(providers, provider_options, disabled_optimizers)
    # 【中文研读】初始化失败路径：允许时尝试后备后端；这仍可能失败并抛出异常，不能把已开启 fallback 理解为模型总能加载成功。
    except (ValueError, RuntimeError) as e:
        if self._enable_fallback:
            try:
                print("*************** EP Error ***************")
                print(f"EP Error {e} when using {providers}")
                print(f"Falling back to {self._fallback_providers} and retrying.")
                print("****************************************")
                self._create_inference_session(self._fallback_providers, None)
                # 【中文研读】本次后端回退之后关掉再次自动回退，避免同一调用无界尝试；P3 仍需对整个调用记录实际耗时和失败。
                self.disable_fallback()
                return
            except Exception as fallback_error:
                raise fallback_error from e
        raise e
```

**执行后得到什么：** Python Session 包装器及底层会话。

**接下来到哪里：** 步骤 08 展开 _create_inference_session。

**失败与 P3 责任：** fallback 不是任意失败都能恢复；最终异常仍须向调用方报告。

<a id="s08"></a>

### 08　展开：底层加载、初始化并缓存图元信息

**当前执行位置：** `InferenceSession._create_inference_session`，完整方法或类型摘录。[出处](../第三方源码中文注释/onnxruntime/capi/onnxruntime_inference_collection.py)，注释版第 769—878 行。

**收到什么：** 可用后端、请求后端、优化器与模型数据。

**这一段怎么处理：** 检查互斥配置，整理 provider 参数，构造 C++ 会话，初始化，保存输入输出元信息。

```python
def _create_inference_session(self, providers, provider_options, disabled_optimizers=None):
    # 【中文研读】处理流程：判断可用后端与回退列表，规范化配置，创建 C.InferenceSession 并初始化 provider 与优化器，最后缓存输入、输出、设备和模型元信息。
    available_providers = C.get_available_providers()

    if providers:
        has_tensorrt = any(
            provider == "TensorrtExecutionProvider"
            or (isinstance(provider, tuple) and provider[0] == "TensorrtExecutionProvider")
            for provider in providers
        )
        has_tensorrt_rtx = any(
            provider == "NvTensorRTRTXExecutionProvider"
            or (isinstance(provider, tuple) and provider[0] == "NvTensorRTRTXExecutionProvider")
            for provider in providers
        )
        # 【中文研读】加载阶段 2：拒绝互斥的 TensorRT 后端组合。以下分支同时计算出错时可尝试的后备后端列表。
        if has_tensorrt and has_tensorrt_rtx:
            raise ValueError(
                "Cannot enable both 'TensorrtExecutionProvider' and 'NvTensorRTRTXExecutionProvider' "
                "in the same session."
            )
    if "NvTensorRTRTXExecutionProvider" in available_providers:
        if (
            providers
            and any(
                provider == "CUDAExecutionProvider"
                or (isinstance(provider, tuple) and provider[0] == "CUDAExecutionProvider")
                for provider in providers
            )
            and any(
                provider == "NvTensorRTRTXExecutionProvider"
                or (isinstance(provider, tuple) and provider[0] == "NvTensorRTRTXExecutionProvider")
                for provider in providers
            )
        ):
            self._fallback_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            self._fallback_providers = ["CPUExecutionProvider"]
    elif "TensorrtExecutionProvider" in available_providers:
        if (
            providers
            and any(
                provider == "CUDAExecutionProvider"
                or (isinstance(provider, tuple) and provider[0] == "CUDAExecutionProvider")
                for provider in providers
            )
            and any(
                provider == "TensorrtExecutionProvider"
                or (isinstance(provider, tuple) and provider[0] == "TensorrtExecutionProvider")
                for provider in providers
            )
        ):
            self._fallback_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            self._fallback_providers = ["CPUExecutionProvider"]
    else:
        self._fallback_providers = ["CPUExecutionProvider"]

    # 【中文研读】加载阶段 3：校验后端名称与选项的组织形式，并转成底层接口需要的列表；配置错误应在执行文本之前暴露。
    providers, provider_options = check_and_normalize_provider_args(
        providers, provider_options, available_providers
    )

    if self._sess_options is not None and (providers or provider_options) and self._sess_options.has_providers():
        warnings.warn(
            "Specified 'providers'/'provider_options' when creating InferenceSession but SessionOptions has "
            "already been configured with providers. InferenceSession will only use the providers "
            "passed to InferenceSession()."
        )

    session_options = self._sess_options if self._sess_options else C.get_default_session_options()

    # 【中文研读】加载阶段 4：为需要的后端注册专用算子；这一动作与加载 P3 业务 Handler 无关。
    self._register_ep_custom_ops(session_options, providers, provider_options, available_providers)

    # 【中文研读】加载阶段 5：将文件路径或模型字节交给 C++ 会话构造器，随后初始化优化器与后端。
    if self._model_path:
        sess = C.InferenceSession(session_options, self._model_path, True, self._read_config_from_model)
    else:
        sess = C.InferenceSession(session_options, self._model_bytes, False, self._read_config_from_model)

    if disabled_optimizers is None:
        disabled_optimizers = set()
    elif not isinstance(disabled_optimizers, set):
        disabled_optimizers = set(disabled_optimizers)

    # 【中文研读】此处完成执行后端和优化器装配；创建会话与执行输入是不同阶段，应分开理解冷启动与单次推理延迟。
    sess.initialize_session(providers, provider_options, disabled_optimizers)

    self._sess = sess
    self._sess_options = self._sess.session_options
    # 【中文研读】加载阶段 6：缓存真实会话的输入输出元信息，供 FastEmbed 构造图输入；元信息来自模型，不是自行猜测字段名。
    self._inputs_meta = self._sess.inputs_meta
    self._outputs_meta = self._sess.outputs_meta
    self._overridable_initializers = self._sess.overridable_initializers
    self._input_meminfos = self._sess.input_meminfos
    self._output_meminfos = self._sess.output_meminfos
    self._input_epdevices = self._sess.input_epdevices
    self._model_meta = self._sess.model_meta
    self._providers = self._sess.get_providers()
    self._provider_options = self._sess.get_provider_options()
    self._profiling_start_time_ns = self._sess.get_profiling_start_time_ns
```

**执行后得到什么：** 后续 get_inputs 与 run 使用的真实图会话。

**接下来到哪里：** 返回加载调用栈；启动阶段到此结束，步骤 09 才是一次查询请求。

**失败与 P3 责任：** 这里没有跨请求持久任务或业务租约。

<a id="s09"></a>

### 09　请求进入：Query 统一入口

**当前执行位置：** `TextEmbedding.query_embed`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/text_embedding.py)，注释版第 226—238 行。

**收到什么：** 查询字符串或多条查询。

**这一段怎么处理：** 保持 Query 角色，转发给 self.model.query_embed。

```python
def query_embed(self, query: str | Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:
    # 【中文研读】处理流程：保持查询角色，转交 self.model.query_embed；模型是否添加查询前缀要继续看具体实现，入口名称本身不保证已添加。
    yield from self.model.query_embed(query, **kwargs)
```

**执行后得到什么：** 生成器；此时尚不能认为向量已经算完。

**接下来到哪里：** 选中的 OnnxTextEmbedding 继承基类 Query 默认实现，进入步骤 10。

**失败与 P3 责任：** 只有消费生成器才会触发后续实际计算与异常。

<a id="s10"></a>

### 10　展开：Query 默认实现规范化批次

**当前执行位置：** `TextEmbeddingBase.query_embed`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/text_embedding_base.py)，注释版第 65—82 行。

**收到什么：** 一条 Query 或 Query 集合。

**这一段怎么处理：** 单字符串包成一个元素的列表；随后调用 self.embed。

```python
def query_embed(self, query: str | Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:

    # 【中文研读】处理流程：单字符串包成列表，其他可迭代输入直接透传；两条分支都交给 embed，不执行关键词查询。
    # 【中文研读】例如 query="我喜欢什么饮料" 时，先转成只有一个元素的批次；不会把字符串当成逐字符文本流。
    if isinstance(query, str):
        yield from self.embed([query], **kwargs)
    else:
        yield from self.embed(query, **kwargs)
```

**执行后得到什么：** 标准批次输入。

**接下来到哪里：** 动态分派到 OnnxTextEmbedding.embed，见步骤 11。

**失败与 P3 责任：** 这条基类路径不添加查询前缀；需要前缀必须按具体模型约定实现。

<a id="s11"></a>

### 11　进入具体编码器：附上实例配置

**当前执行位置：** `OnnxTextEmbedding.embed`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_embedding.py)，注释版第 278—314 行。

**收到什么：** 文本批次与 batch_size、parallel。

**这一段怎么处理：** 把模型目录、后端、设备与会话选项一起传给共用批处理方法。

```python
def embed(
    self,
    documents: str | Iterable[str],
    batch_size: int = 256,
    parallel: int | None = None,
    **kwargs: Any,
) -> Iterable[NumpyArray]:
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
```

**执行后得到什么：** _embed_documents 的输出迭代器。

**接下来到哪里：** 步骤 12 选择执行分支。

**失败与 P3 责任：** 本步骤只是转发，尚未看到图的输出张量。

<a id="s12"></a>

### 12　按批次执行；小输入留在当前进程

**当前执行位置：** `OnnxTextModel._embed_documents`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_text_model.py)，注释版第 146—207 行。

**收到什么：** 文本、批大小和并行设置。

**这一段怎么处理：** 统一字符串形状；默认/小输入在本进程按需加载，分批执行 onnx_embed，再把每批交给后处理；另保留可选进程池分支。

```python
def _embed_documents(
    self,
    model_name: str,
    cache_dir: str,
    documents: str | Iterable[str],
    batch_size: int = 256,
    parallel: int | None = None,
    providers: Sequence[OnnxProvider] | None = None,
    cuda: bool | Device = Device.AUTO,
    device_ids: list[int] | None = None,
    local_files_only: bool = False,
    specific_model_path: str | None = None,
    extra_session_options: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Iterable[T]:
    # 【中文研读】处理流程：规范化单字符串，识别小批；本进程按需加载并逐批推理，多进程用有序 Worker 池保持批次顺序；每批输出都经过模型专用后处理。
    is_small = False

    if isinstance(documents, str):
        documents = [documents]
        is_small = True

    if isinstance(documents, list):
        if len(documents) < batch_size:
            is_small = True

    # 【中文研读】执行分支 A：默认或小输入留在当前进程，避免进程启动成本。yield from 的消费过程应纳入超时和 trace 范围。
    if parallel is None or is_small:
        if not hasattr(self, "model") or self.model is None:
            self.load_onnx_model()
        for batch in iter_batch(documents, batch_size):
            yield from self._post_process_onnx_output(
                self.onnx_embed(batch, **kwargs), **kwargs
            )
    else:
        if parallel == 0:
            parallel = os.cpu_count()

        # 【中文研读】执行分支 B：为较大输入准备子进程；根据系统支持选择启动方式，Windows 通常走 spawn。
        start_method = "forkserver" if "forkserver" in get_all_start_methods() else "spawn"
        params = {
            "model_name": model_name,
            "cache_dir": cache_dir,
            "providers": providers,
            "local_files_only": local_files_only,
            "specific_model_path": specific_model_path,
            **kwargs,
        }

        if extra_session_options is not None:
            params.update(extra_session_options)

        pool = ParallelWorkerPool(
            num_workers=parallel or 1,
            worker=self._get_worker_class(),
            cuda=cuda,
            device_ids=device_ids,
            start_method=start_method,
        )
        # 【中文研读】有序收集计算结果，避免并行完成次序改变“文本→向量”对应关系；顺序保证不等于业务写入幂等。
        for batch in pool.ordered_map(iter_batch(documents, batch_size), **params):
            yield from self._post_process_onnx_output(batch, **kwargs)  # type: ignore
```

**执行后得到什么：** 按输入顺序生成的编码结果流。

**接下来到哪里：** 本次单进程分支进入步骤 13；推理返回后到步骤 19 后处理。

**失败与 P3 责任：** 本地 Worker 池没有持久恢复机制；不要另建一套业务任务系统。

<a id="s13"></a>

### 13　准备一次 ONNX 推理：先分词

**当前执行位置：** `OnnxTextModel.onnx_embed`，方法内部片段。[出处](../第三方源码中文注释/fastembed/text/onnx_text_model.py)，注释版第 110—117 行。

**收到什么：** 一批字符串 documents。

**这一段怎么处理：** 第一句调用 tokenize；当前先暂停在此调用点，展开下一步。

```python
def onnx_embed(
    self,
    documents: list[str],
    **kwargs: Any,
) -> OnnxOutputContext:
    # 【中文研读】处理流程：分词→构建 int64 输入→按图要求添加掩码和类型 IDs→模型专用预处理→Session.run→连同输入信息返回原始输出。
    # 【中文研读】阶段 1：先分词，得到 input_ids 与 attention_mask；掩码区分有效 token 与补齐位置。
    encoded = self.tokenize(documents, **kwargs)
```

**执行后得到什么：** 等待分词结果。

**接下来到哪里：** 步骤 14。

**失败与 P3 责任：** 不能把原始字符串直接送给图；图要求特定名称和类型的张量。

<a id="s14"></a>

### 14　展开：tokenizer 批量编码

**当前执行位置：** `OnnxTextModel.tokenize`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_text_model.py)，注释版第 103—105 行。

**收到什么：** 同一批文本。

**这一段怎么处理：** 调用配套 tokenizer.encode_batch。

```python
def tokenize(self, documents: list[str], **kwargs: Any) -> list[Encoding]:
    # 【中文研读】处理流程：使用已加载 tokenizer 的 encode_batch，返回每条文本的 IDs 和掩码；padding、截断等行为来自 tokenizer 配置。
    return self.tokenizer.encode_batch(documents)  # type: ignore[union-attr]
```

**执行后得到什么：** 每条文本的 IDs、attention_mask 等编码结果。

**接下来到哪里：** 返回 onnx_embed，继续步骤 15 构造张量。

**失败与 P3 责任：** 长度截断/padding 由 tokenizer 配置影响；P3 应验证是否发生内容截断。

<a id="s15"></a>

### 15　回到推理：按图声明组装输入

**当前执行位置：** `OnnxTextModel.onnx_embed`，方法内部片段。[出处](../第三方源码中文注释/fastembed/text/onnx_text_model.py)，注释版第 118—133 行。

**收到什么：** 分词输出与会话输入元信息。

**这一段怎么处理：** 构造 int64 input_ids；只有图声明需要时才加 attention_mask/token_type_ids；调用模型专用输入钩子。

```python
input_ids = np.array([e.ids for e in encoded])
attention_mask = np.array([e.attention_mask for e in encoded])
# 【中文研读】阶段 2：从图读取实际输入名称，避免把模型不接受的可选字段硬塞给运行时。
input_names = {node.name for node in self.model.get_inputs()}  # type: ignore[union-attr]
onnx_input: dict[str, NumpyArray] = {
    "input_ids": np.array(input_ids, dtype=np.int64),
}
if "attention_mask" in input_names:
    onnx_input["attention_mask"] = np.array(attention_mask, dtype=np.int64)
# 【中文研读】某些编码图要求句段类型 ID；这里全部填 0，表示单段编码，不是 CrossEncoder 的查询正文联合建模。
if "token_type_ids" in input_names:
    onnx_input["token_type_ids"] = np.array(
        [np.zeros(len(e), dtype=np.int64) for e in input_ids], dtype=np.int64
    )
onnx_input = self._preprocess_onnx_input(onnx_input, **kwargs)
```

**执行后得到什么：** onnx_input 字典。

**接下来到哪里：** 输入钩子见步骤 16；接着 Session.run 进入步骤 17。

**失败与 P3 责任：** get_inputs 的元信息由启动阶段缓存；传错名称、类型或形状应视为明确依赖失败。

<a id="s16"></a>

### 16　展开：这个模型的输入钩子原样返回

**当前执行位置：** `OnnxTextEmbedding._preprocess_onnx_input`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_embedding.py)，注释版第 327—334 行。

**收到什么：** 已经匹配图输入的字典。

**这一段怎么处理：** 该具体实现不做额外变换。

```python
def _preprocess_onnx_input(
    self, onnx_input: dict[str, NumpyArray], **kwargs: Any
) -> dict[str, NumpyArray]:
    # 【中文研读】处理流程：此实现原样返回字典；其他模型可以覆盖此钩子，不能推定所有模型输入完全相同。
    return onnx_input
```

**执行后得到什么：** 同一个 onnx_input。

**接下来到哪里：** 返回 onnx_embed，执行 self.model.run，展开步骤 17。

**失败与 P3 责任：** 其他具体模型可以覆盖钩子，不能把此行为推广到所有 FastEmbed 模型。

<a id="s17"></a>

### 17　实际推理：校验后进入 C++，必要时有限回退

**当前执行位置：** `Session.run`，完整方法或类型摘录。[出处](../第三方源码中文注释/onnxruntime/capi/onnxruntime_inference_collection.py)，注释版第 418—456 行。

**收到什么：** 输出名称和输入张量。

**这一段怎么处理：** 先检查输入，再调用底层 _sess.run；仅特定 provider 失败时可更换后端后重试一次。

```python
def run(self, output_names, input_feed, run_options=None) -> Sequence[np.ndarray | SparseTensor | list | dict]:
    # 【中文研读】处理流程：检查图捕获限制、必需输入和 OrtValue 所有权，补全输出名称，再调用 C++ run；仅 EPFail 在允许时触发一次后端重建回退，其他错误直接传播。
    self._validate_graph_capture_run_api(run_options)
    self._validate_input(list(input_feed.keys()))
    self._validate_ortvalue_ownership(input_feed.values())
    if not output_names:
        output_names = [output.name for output in self._outputs_meta]
    try:
        # 【中文研读】执行边界：数值计算进入底层 C++ 会话；Python 包装层不实现 Transformer 算子，也不校验业务记忆资格。
        return self._sess.run(output_names, input_feed, run_options)
    # 【中文研读】只捕获执行后端失败。输入错误、业务取消和数据库故障不属于这里的自动回退范围。
    except C.EPFail as err:
        if self._enable_fallback:
            print(f"EP Error: {err!s} using {self._providers}")
            print(f"Falling back to {self._fallback_providers} and retrying.")
            self.set_providers(self._fallback_providers)
            # 【中文研读】本次后端回退之后关掉再次自动回退，避免同一调用无界尝试；P3 仍需对整个调用记录实际耗时和失败。
            self.disable_fallback()
            self._validate_ortvalue_ownership(input_feed.values())
            # 【中文研读】执行边界：数值计算进入底层 C++ 会话；Python 包装层不实现 Transformer 算子，也不校验业务记忆资格。
            return self._sess.run(output_names, input_feed, run_options)
        raise
```

**执行后得到什么：** 原始图输出列表。

**接下来到哪里：** 返回 onnx_embed，步骤 18 取出输出并封装。

**失败与 P3 责任：** 后端回退不是 P3 业务恢复；模型超时也不能被转换成空数组冒充成功。

<a id="s18"></a>

### 18　返回上层：保留原始输出与掩码

**当前执行位置：** `OnnxTextModel.onnx_embed`，方法内部片段。[出处](../第三方源码中文注释/fastembed/text/onnx_text_model.py)，注释版第 134—141 行。

**收到什么：** Session.run 的输出列表及输入字典。

**这一段怎么处理：** 取第一个图输出，连同掩码与 token IDs 构成 OnnxOutputContext。

```python
# 【中文研读】阶段 3：真正进入 ONNX Runtime，此处才产生原始向量张量。异常向调用者传播，不会变成空向量冒充成功。
model_output = self.model.run(self.ONNX_OUTPUT_NAMES, onnx_input)  # type: ignore[union-attr]
# 【中文研读】阶段 4：仅取图的第一个输出，并保留掩码和 token IDs，交给具体模型后处理；它还不是带 memory_id/version 的 P3 候选。
return OnnxOutputContext(
    model_output=model_output[0],
    attention_mask=onnx_input.get("attention_mask", attention_mask),
    input_ids=onnx_input.get("input_ids", input_ids),
)
```

**执行后得到什么：** 交给 _embed_documents 的原始输出上下文。

**接下来到哪里：** _embed_documents 随即调用步骤 19 的后处理。

**失败与 P3 责任：** 此时还不一定是每段文本一个向量，也没有绑定 memory_id/version。

<a id="s19"></a>

### 19　把原始输出变成最终句向量

**当前执行位置：** `OnnxTextEmbedding._post_process_onnx_output`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/onnx_embedding.py)，注释版第 339—354 行。

**收到什么：** 可能为 [批次,位置,维度] 或 [批次,维度] 的图输出。

**这一段怎么处理：** 三维取首 token，二维直接使用，其他形状报错；再归一化。

```python
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
```

**执行后得到什么：** 每条输入对应的稠密向量。

**接下来到哪里：** 沿 yield from 返回 P3 调用方；P3 核对后才使用。

**失败与 P3 责任：** 本实现是首 token 路径，不能被英文概述中的 mean pooling 误导；更换池化会改变模型空间。

<a id="s20"></a>

### 20　正文入口的区别：保持 Passage 角色

**当前执行位置：** `TextEmbedding.passage_embed`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/text_embedding.py)，注释版第 243—256 行。

**收到什么：** Remember 待建立投影的正文。

**这一段怎么处理：** 转发给具体模型的 passage_embed。

```python
def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:
    # 【中文研读】处理流程：转交具体实现的 passage_embed 并逐条返回向量。本入口属于稠密编码；下方英文 Yields 写成 SparseEmbedding 与实际类路径不符，原文保留用于对照。
    yield from self.model.passage_embed(texts, **kwargs)
```

**执行后得到什么：** 正文向量迭代器。

**接下来到哪里：** 本路径的默认实现见步骤 21；随后复用步骤 11—19。

**失败与 P3 责任：** 上游英文返回说明有残留命名，实际类路径是稠密编码。

<a id="s21"></a>

### 21　正文默认路径仍调用同一个 embed

**当前执行位置：** `TextEmbeddingBase.passage_embed`，完整方法或类型摘录。[出处](../第三方源码中文注释/fastembed/text/text_embedding_base.py)，注释版第 45—60 行。

**收到什么：** 正文集合。

**这一段怎么处理：** 原样调用 self.embed。

```python
def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[NumpyArray]:

    # 【中文研读】处理流程：不添加前缀、不查询向量库，通过生成器逐条传回子类产物。
    # 【中文研读】正文与查询都生成稠密向量；这两个角色入口不意味着采用两种索引，也不意味着一定使用两套模型。
    yield from self.embed(texts, **kwargs)
```

**执行后得到什么：** 与 Query 相同空间约定下的正文向量。

**接下来到哪里：** P3 投影任务接收结果，核验版本后写向量引用。

**失败与 P3 责任：** 角色入口不等于两种索引；是否使用不同前缀由选定模型约定决定。

## 本章出现的外部调用与停止展开的位置

| 调用或能力 | 在这条链中的作用 | 为什么在这里画边界 |
|---|---|---|
| 模型注册表 / _get_model_description / download_model | 根据名称定位支持配置和权重文件 | 模型文件管理边界；本文保留调用点，不展开全部下载后端 |
| load_tokenizer / tokenizer.encode_batch | 读取配套分词规则并批量分词 | tokenizers 本地组件边界；不是下游语言模型的预算 tokenizer |
| C.InferenceSession / _sess.run | 加载计算图并在底层运行算子 | C++ 数值推理边界；不逐层展开 Transformer 算子 |
| iter_batch / normalize / NumPy | 批次切分、向量归一化和数组组织 | 通用数值辅助函数；正文中解释输入输出，不展开所有数组库方法 |
| ParallelWorkerPool | 可选多进程编码 | 默认请求沿单进程分支；并行分支保留在摘录中，不进入其内部调度 |

## 对照 P3 应怎样使用

P3 通过自己的 EmbeddingPort 约束 Query/Passage 用途与模型空间。FastEmbed 返回的数组必须与请求中的输入 ID 对齐，核对数量、维度和数值，才能交给投影或查询。

如果模型卡要求 Query 前缀，应确认具体模型路径在哪里加入，避免漏加或重复添加；基类默认 query_embed 没有自动添加。FastEmbed 返回迭代器，因此 trace 和超时观测应覆盖消费迭代器的整个过程。

默认正文和查询编码分别计算，可以预先保存正文向量。下一章的 LlamaIndex Embedding 接口与 FastEmbed 入口不是自动连接的；需要兼容适配器，本章展示的是可复用的编码实现机制。
