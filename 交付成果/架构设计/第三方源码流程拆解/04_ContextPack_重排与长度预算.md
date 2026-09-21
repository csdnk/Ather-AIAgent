# 从候选到 ContextPack：重排和长度预算怎样衔接

[返回流程总入口](README.md)

本章连接两个独立的可复用计算能力：CrossEncoder 给已有候选打分；tiktoken 计算最终文本长度。它们之间的资格核验、内容选择与 ContextPack 组装属于 P3。源码中没有一个现成方法会自动完成这整条产品流程。

## 先看这一条执行路径

```text
已有合法候选 → CrossEncoder.rank
  → 组 Query/Doc 对 → predict
  → 规范输入 → 按长度组批 → 联合预处理 → 模型前向
  → 分数变换 → 恢复输入顺序
  ← rank 绑定 corpus_id → 降序选 TopK
P3：绑定 memory_id/version → 处理冲突/来源 → 组装候选上下文文本
  → tiktoken.get_encoding → Encoding.encode → len(ids)
P3：根据预算选择完整单元 → 重组并重新计数 → 最终资格核验 → ContextPack
```

贯穿示例：已获准的候选 A 是“最终批准预算十五万元”，B 是“上季度支出十五万元”。问题是“最终批准预算多少”。CrossEncoder 为这两对输入分别评分；预算统计的是最终带来源说明的上下文，不只是 A/B 的裸正文。分数和 token 数未在此伪造为实测结果。

以下代码从已核对版本逐段摘录；省略导入、英文说明文档及英文整行注释，保留执行语句、字符串与中文研读注释。标为“片段”的代码保留原方法的局部上下文，不能当成独立函数运行。

## 按执行顺序展开

- [步骤 01：装配打分模型，绑定长度和输出配置](#s01)
- [步骤 02：展开：不同模型用不同打分模块](#s02)
- [步骤 03：rank 请求进入：构造查询与候选对](#s03)
- [步骤 04：predict：校验并统一单对/多对输入](#s04)
- [步骤 05：展开：识别输入是单对还是批次](#s05)
- [步骤 06：联合输入打分：长度排序、分批、前向计算](#s06)
- [步骤 07：把分数还给正确的候选](#s07)
- [步骤 08：rank 返回：保留下标后排序截取](#s08)
- [步骤 09：预算开始：按明确名称取得 tokenizer](#s09)
- [步骤 10：展开：发现编码器的构造函数](#s10)
- [步骤 11：展开：找可提供编码表的插件](#s11)
- [步骤 12：装配词表与 Rust BPE 实例](#s12)
- [步骤 13：编码文本并获得 token ID 列表](#s13)
- [步骤 14：若需要解码：先理解字符边界风险](#s14)
- [步骤 15：展开：token ID 先还原为字节](#s15)

<a id="s01"></a>

### 01　装配打分模型，绑定长度和输出配置

**当前执行位置：** `CrossEncoder.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 155—218 行。

**收到什么：** 重排模型名称、num_labels、max_length、设备和激活函数。

**这一段怎么处理：** 把输出数和最大长度放入配置，交父类加载模块，确定激活函数。

```python
@cross_encoder_init_args_decorator
def __init__(
    self,
    model_name_or_path: str | None = None,
    *,
    modules: list[nn.Module] | OrderedDict[str, nn.Module] | None = None,
    device: str | None = None,
    prompts: dict[str, str] | None = None,
    default_prompt_name: str | None = None,
    cache_folder: str | None = None,
    trust_remote_code: bool = False,
    revision: str | None = None,
    local_files_only: bool = False,
    token: bool | str | None = None,
    model_kwargs: dict | None = None,
    processor_kwargs: dict | None = None,
    config_kwargs: dict | None = None,
    model_card_data: CrossEncoderModelCardData | None = None,
    backend: Literal["torch", "onnx", "openvino"] = "torch",
    num_labels: int | None = None,
    max_length: int | None = None,
    activation_fn: Callable | None = None,
) -> None:
    # 【中文研读】处理流程：将标签数和长度限制写入模型/处理器配置，交由 BaseModel 加载模块；优先采用显式激活函数，否则沿用已加载或默认配置。
    self.activation_fn = None

    # 【中文研读】构造阶段：输出维数属于模型配置；rank 要求每对输入一个分数，不能把多分类向量直接当排序分值。
    if num_labels is not None:
        if config_kwargs is None:
            config_kwargs = {}
        config_kwargs["num_labels"] = num_labels

    if max_length is not None:
        if processor_kwargs is None:
            processor_kwargs = {}
        processor_kwargs["model_max_length"] = max_length

    super().__init__(
        model_name_or_path=model_name_or_path,
        modules=modules,
        device=device,
        cache_folder=cache_folder,
        trust_remote_code=trust_remote_code,
        revision=revision,
        local_files_only=local_files_only,
        token=token,
        model_kwargs=model_kwargs,
        processor_kwargs=processor_kwargs,
        config_kwargs=config_kwargs,
        model_card_data=model_card_data,
        backend=backend,
        prompts=prompts,
        default_prompt_name=default_prompt_name,
    )
    self.model_card_data: CrossEncoderModelCardData

    if activation_fn is not None:
        self.activation_fn = activation_fn
    elif self.activation_fn is None:
        self.activation_fn = self.get_default_activation_fn()
```

**执行后得到什么：** 可重复使用的重排模型实例。

**接下来到哪里：** 首次模块选择见步骤 02；有请求时进入 rank。

**失败与 P3 责任：** 选择模型不只是包名；权重、版本和输入长度限制都影响结果。

<a id="s02"></a>

### 02　展开：不同模型用不同打分模块

**当前执行位置：** `CrossEncoder._load_default_modules`，完整方法或类型摘录。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 223—294 行。

**收到什么：** 模型配置与加载选项。

**这一段怎么处理：** 读取 AutoConfig：因果模型用生成模块加 yes/no 分数处理，其他模型走序列分类。

```python
def _load_default_modules(
    self,
    model_name_or_path: str,
    token: bool | str | None,
    cache_folder: str | None,
    revision: str | None = None,
    trust_remote_code: bool = False,
    local_files_only: bool = False,
    model_kwargs: dict[str, Any] | None = None,
    processor_kwargs: dict[str, Any] | None = None,
    config_kwargs: dict[str, Any] | None = None,
) -> tuple[list[nn.Module] | OrderedDict[str, nn.Module], dict[str, Any]]:
    # 【中文研读】处理流程：合并版本、缓存和本地加载选项；读取 AutoConfig，因果模型使用生成 Transformer 加 yes/no LogitScore，其他模型使用序列分类 Transformer。不是所有 CrossEncoder 都采用同一架构。
    shared_kwargs = {
        "token": token,
        "trust_remote_code": trust_remote_code,
        "revision": revision,
        "local_files_only": local_files_only,
    }
    model_kwargs = {**shared_kwargs} if model_kwargs is None else {**shared_kwargs, **model_kwargs}
    processor_kwargs = {**shared_kwargs} if processor_kwargs is None else {**shared_kwargs, **processor_kwargs}
    config_kwargs = {**shared_kwargs} if config_kwargs is None else {**shared_kwargs, **config_kwargs}

    if not local_files_only:
        self.model_card_data.set_base_model(model_name_or_path, revision=revision)

    # 【中文研读】加载模型配置后才知道该用序列分类还是因果模型打分；revision 和预处理也需要纳入 P3 模型配置版本。
    config: PretrainedConfig = AutoConfig.from_pretrained(
        model_name_or_path,
        cache_dir=cache_folder,
        **config_kwargs,
    )
    if (
        hasattr(config, "architectures")
        and config.architectures is not None
        and config.architectures[0].endswith("ForCausalLM")
    ):
        transformer_model = Transformer(
            model_name_or_path,
            transformer_task="text-generation",
            cache_dir=cache_folder,
            model_kwargs=model_kwargs,
            processor_kwargs=processor_kwargs,
            config_kwargs=config_kwargs,
            backend=self.backend,
        )
        true_token_id = transformer_model.tokenizer.convert_tokens_to_ids("yes")
        false_token_id = transformer_model.tokenizer.convert_tokens_to_ids("no")
        if true_token_id is None or false_token_id is None:
            raise ValueError(
                "The tokenizer does not have 'yes' and/or 'no' tokens, which are used as the "
                "default true/false tokens for the LogitScore post-processing module. Please "
                "provide custom modules with your desired LogitScore configuration, or use a "
                "model with a tokenizer that supports these tokens."
            )
        post_processing = LogitScore(
            true_token_id=true_token_id,
            false_token_id=false_token_id,
        )
        return [transformer_model, post_processing], {}

    transformer_model = Transformer(
        model_name_or_path,
        transformer_task="sequence-classification",
        cache_dir=cache_folder,
        model_kwargs=model_kwargs,
        processor_kwargs=processor_kwargs,
        config_kwargs=config_kwargs,
        backend=self.backend,
    )
    return [transformer_model], {}
```

**执行后得到什么：** 可生成 scores 的模块列表。

**接下来到哪里：** 返回构造链；之后进入步骤 03。

**失败与 P3 责任：** 不是所有 CrossEncoder 都是同一种 BERT 分类器；某些模型缺少预期 token 时加载会失败。

<a id="s03"></a>

### 03　rank 请求进入：构造查询与候选对

**当前执行位置：** `CrossEncoder.rank`，方法内部片段。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 854—876 行。

**收到什么：** Query 与候选正文列表。

**这一段怎么处理：** 校验单标签输出，构造 [[query,docA],[query,docB]]，调用 predict。

```python
# 【中文研读】处理流程：要求单标签模型，调用 predict，给分数附原列表位置 corpus_id，降序排序取 top_k；此位置不是 memory_id，P3 必须保留外部映射。
if self.num_labels != 1:
    raise ValueError(
        "CrossEncoder.rank() only works for models with num_labels=1. "
        "Consider using CrossEncoder.predict() with input pairs instead."
    )
# 【中文研读】rank 将 Query 分别与每条正文配对。输入应先通过 P3 授权与版本筛选；评分较高并不证明有访问权限。
query_doc_pairs: list[PairInput] = [[query, doc] for doc in documents]
scores = self.predict(
    inputs=query_doc_pairs,
    prompt_name=prompt_name,
    prompt=prompt,
    batch_size=batch_size,
    show_progress_bar=show_progress_bar,
    activation_fn=activation_fn,
    apply_softmax=apply_softmax,
    convert_to_numpy=convert_to_numpy,
    convert_to_tensor=convert_to_tensor,
    device=device,
    pool=pool,
    chunk_size=chunk_size,
)
```

**执行后得到什么：** 等待逐对分数。

**接下来到哪里：** 步骤 04 展开 predict；返回后继续步骤 08。

**失败与 P3 责任：** 传入正文之前已应通过授权和版本检查；rank 不会自行检查。

<a id="s04"></a>

### 04　predict：校验并统一单对/多对输入

**当前执行位置：** `CrossEncoder.predict`，方法内部片段。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 657—703 行。

**收到什么：** 成对输入及批大小、设备、输出格式。

**这一段怎么处理：** 检查 batch_size，判断单对输入并规范为列表；池或多设备有独立路径。

```python
# 【中文研读】处理流程：以下同名 overload 是静态类型声明，真正执行在带 inference_mode 的方法中；校验批大小，规范化输入，选择设备路径，按长度组批→联合预处理→前向计算→激活与形状调整→恢复原顺序→转换结果类型。错误向调用方传播。
if show_progress_bar is None:
    show_progress_bar = (
        logger.getEffectiveLevel() == logging.INFO or logger.getEffectiveLevel() == logging.DEBUG
    )

# 【中文研读】阶段 1：尽早拒绝无效批大小，不能把没有执行的批次记成成功。
if batch_size <= 0:
    raise ValueError(f"batch_size must be a positive integer, got {batch_size}.")

# 【中文研读】阶段 2：把单个 [query,doc] 规范化成一批，后面所有处理使用一致的批次接口。
is_singular_input = self.is_singular_input(inputs)
if is_singular_input:
    if isinstance(inputs, np.ndarray):
        inputs = inputs.tolist()
    inputs = [inputs]
elif not isinstance(inputs, list):
    inputs = inputs.tolist() if isinstance(inputs, np.ndarray) else list(inputs)

# 【中文研读】可选分支：多设备/外部池进入 _multi_process。本次研读主线是单设备分批推理，持久任务并发仍由 P3 控制。
if pool is not None or (isinstance(device, list) and len(device) > 0):
    pred_scores = self._multi_process(
        inputs=inputs,
        show_progress_bar=show_progress_bar,
        pool=pool,
        device=device,
        chunk_size=chunk_size,
        prompt=prompt,
        prompt_name=prompt_name,
        batch_size=batch_size,
        activation_fn=activation_fn,
        apply_softmax=apply_softmax,
        convert_to_numpy=convert_to_numpy,
        convert_to_tensor=convert_to_tensor,
        **kwargs,
    )
    if is_singular_input:
        pred_scores = pred_scores[0]
    return pred_scores
```

**执行后得到什么：** 本次规范化的 input pairs。

**接下来到哪里：** 形状判断展开在步骤 05；单设备主线继续步骤 06。

**失败与 P3 责任：** 多设备分支保留作为旁支，不把其进程池当作 P3 持久任务。

<a id="s05"></a>

### 05　展开：识别输入是单对还是批次

**当前执行位置：** `CrossEncoder.is_singular_input`，完整方法或类型摘录。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 891—916 行。

**收到什么：** 列表、元组或数组输入。

**这一段怎么处理：** 按第一层元素类型、数组维数和空值判断。

```python
def is_singular_input(self, inputs: PairInput | list[PairInput]) -> bool:
    # 【中文研读】处理流程：列表看第一层元素，NumPy 字符串/对象数组按维数和是否为空判断；这是形状约定，不负责检查文本内容是否可信。
    list_types = (list, tuple)
    if is_datasets_available():
        try:
            from datasets import Column

            list_types += (Column,)
        except ImportError:
            pass
    if isinstance(inputs, list_types):
        return len(inputs) > 0 and not isinstance(inputs[0], list_types)
    if isinstance(inputs, np.ndarray) and inputs.dtype.kind in ("U", "O"):
        if inputs.size == 0:
            return False
        return inputs.ndim < 2
    return True
```

**执行后得到什么：** is_singular_input 布尔值。

**接下来到哪里：** 回到 predict，再执行单设备推理段。

**失败与 P3 责任：** 这是形状判断，不保证输入真的是合法 Query/Doc；适配器应限制输入类型。

<a id="s06"></a>

### 06　联合输入打分：长度排序、分批、前向计算

**当前执行位置：** `CrossEncoder.predict`，方法内部片段。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 704—745 行。

**收到什么：** 规范化候选对。

**这一段怎么处理：** 选设备并进入推理模式，按长度排列输入；每批联合预处理并移到设备，模型返回 scores，再做激活/softmax/维度处理。

```python
prompt = self._resolve_prompt(prompt, prompt_name)

if device is None:
    device = str(self.device)

self.to(device)

# 【中文研读】阶段 3：进入评估模式，方法装饰器关闭梯度计算；不会在本次评分中训练模型。
self.eval()
activation_fn = activation_fn or self.activation_fn
num_labels = self.num_labels

pred_scores = []
# 【中文研读】阶段 4：先按长度组织批次，减少 padding 的浪费；因此返回前必须恢复原索引，防止把 A 的分数贴到 B。
length_sorted_idx = np.argsort([-self._input_length(pair) for pair in inputs])
if self._can_flatten_inputs():
    length_sorted_idx = self._interleave_sorted_indices(length_sorted_idx)
inputs_sorted = [inputs[idx] for idx in length_sorted_idx]
for start_index in trange(0, len(inputs_sorted), batch_size, desc="Batches", disable=not show_progress_bar):
    batch = inputs_sorted[start_index : start_index + batch_size]
    # 【中文研读】阶段 5：把查询与正文作为联合输入预处理；例如 ["喜欢什么饮料", "我喜欢绿茶"] 在一次模型调用中共同参与评分。
    features = self.preprocess(batch, prompt=prompt, **kwargs)
    features = batch_to_device(features, device)
    # 【中文研读】阶段 6：真正模型前向计算，按模块链返回 scores；此处不访问 Milvus，也不生成给用户的自然语言回答。
    out_features = self(features, **kwargs)
    scores = out_features["scores"]

    if activation_fn is not None:
        # 【中文研读】分数可能经过 sigmoid 或其他映射；不能仅因取值在 0～1 就当作业务正确概率，不同模型分数阈值不能直接通用。
        scores = activation_fn(scores)

    if apply_softmax and scores.ndim > 1:
        scores = torch.nn.functional.softmax(scores, dim=1)

    if num_labels == 1 and scores.ndim > 1:
        scores = scores.squeeze(-1)

    pred_scores.extend(scores)
```

**执行后得到什么：** 暂时按计算次序排列的分数。

**接下来到哪里：** 步骤 07 恢复原输入顺序。

**失败与 P3 责任：** 长度排序是计算优化，不是相关性排序；激活后的 0～1 分数也不能直接称为事实正确概率。

<a id="s07"></a>

### 07　把分数还给正确的候选

**当前执行位置：** `CrossEncoder.predict`，方法内部片段。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 746—760 行。

**收到什么：** 分数列表和 length_sorted_idx。

**这一段怎么处理：** 逆置排序恢复输入次序，再转 Tensor/NumPy；单对输入拆包。

```python
# 【中文研读】阶段 7：逆置排序，保证第 i 个分数仍对应调用者的第 i 对输入。P3 将它重新绑定到 memory_id + version。
pred_scores = [pred_scores[idx] for idx in np.argsort(length_sorted_idx)]

if convert_to_tensor:
    if len(pred_scores):
        pred_scores = torch.stack(pred_scores)
    else:
        pred_scores = torch.tensor([], device=device)
elif convert_to_numpy:
    pred_scores = np.asarray([score.cpu().detach().float().numpy() for score in pred_scores])

if is_singular_input:
    pred_scores = pred_scores[0]

return pred_scores
```

**执行后得到什么：** 第 i 个分数对应原第 i 对输入。

**接下来到哪里：** 返回 rank，见步骤 08。

**失败与 P3 责任：** 如果适配器省掉或破坏这个映射，就会把 A 的分数贴给 B。

<a id="s08"></a>

### 08　rank 返回：保留下标后排序截取

**当前执行位置：** `CrossEncoder.rank`，方法内部片段。[出处](../第三方源码中文注释/sentence_transformers/cross_encoder/model.py)，注释版第 877—886 行。

**收到什么：** 按原输入顺序排列的 scores。

**这一段怎么处理：** 给每条分数附 corpus_id 和可选原文；按分数降序，截 top_k。

```python
results = []
for i, score in enumerate(scores):
    results.append({"corpus_id": i, "score": score})
    if return_documents:
        results[-1].update({"text": documents[i]})

# 【中文研读】最后按分数排序；P3 仍需规定同分规则、重排超时策略，并在交付 ContextPack 前再次核验当前版本和删除状态。
results = sorted(results, key=lambda x: x["score"], reverse=True)
return results[:top_k]
```

**执行后得到什么：** 重排后的候选位置与分数。

**接下来到哪里：** P3 按 corpus_id 映射业务 ID，处理冲突并组装待交付文本；随后才进入 token 计数。

**失败与 P3 责任：** 高分不能让已失效记忆恢复资格；最终核验仍必需。

<a id="s09"></a>

### 09　预算开始：按明确名称取得 tokenizer

**当前执行位置：** `get_encoding`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/registry.py)，注释版第 78—106 行。

**收到什么：** 例如配置的 o200k_base 名称。

**这一段怎么处理：** 先查缓存，持锁再次检查；缺少注册表时加载构造器，未知名称报错，成功构造后缓存。

```python
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
```

**执行后得到什么：** Encoding 对象。

**接下来到哪里：** 首次进入步骤 10—12；已有缓存则直接到步骤 13。

**失败与 P3 责任：** 固定名称是可复现实验口径，不代表下游模型已经选定；不能静默换编码器。

<a id="s10"></a>

### 10　展开：发现编码器的构造函数

**当前执行位置：** `_find_constructors`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/registry.py)，注释版第 42—70 行。

**收到什么：** 尚未加载的编码注册表。

**这一段怎么处理：** 遍历插件模块，检查构造器导出和重复名称；失败清空半成品再抛错。

```python
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
            # 【中文研读】注册失败恢复到未初始化状态并保留异常，避免其他调用使用不完整的构造器表。
            ENCODING_CONSTRUCTORS = None
            raise
```

**执行后得到什么：** 名称到构造函数的映射。

**接下来到哪里：** 插件枚举见步骤 11；完成后回到 get_encoding。

**失败与 P3 责任：** 缓存与锁均为本进程机制，不是分布式配置一致性。

<a id="s11"></a>

### 11　展开：找可提供编码表的插件

**当前执行位置：** `_available_plugin_modules`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/registry.py)，注释版第 24—36 行。

**收到什么：** tiktoken_ext 命名空间。

**这一段怎么处理：** 扫描子模块并缓存名称列表。

```python
@functools.lru_cache
def _available_plugin_modules() -> Sequence[str]:
    # 【中文研读】处理流程：扫描 tiktoken_ext 命名空间，缓存模块名称列表；尚未把某段文本编码。
    mods = []
    plugin_mods = pkgutil.iter_modules(tiktoken_ext.__path__, tiktoken_ext.__name__ + ".")
    for _, mod_name, _ in plugin_mods:
        mods.append(mod_name)
    return mods
```

**执行后得到什么：** 插件模块名列表。

**接下来到哪里：** 返回构造器发现函数，随后调用 Encoding 构造。

**失败与 P3 责任：** 这不等于每个词表已经加载，也没有文本编码结果。

<a id="s12"></a>

### 12　装配词表与 Rust BPE 实例

**当前执行位置：** `Encoding.__init__`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/core.py)，注释版第 23—65 行。

**收到什么：** 构造器返回的正则、词表与特殊 token。

**这一段怎么处理：** 保存规则，检查可选词表大小，创建 CoreBPE。

```python
def __init__(
    self,
    name: str,
    *,
    pat_str: str,
    mergeable_ranks: dict[bytes, int],
    special_tokens: dict[str, int],
    explicit_n_vocab: int | None = None,
):
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

    self._special_token_values = set(self._special_tokens.values())

    # 【中文研读】阶段 1：连接高性能 Rust 实现，Python 层保留参数与策略；此对象既不是语言模型，也不生成语义向量。
    self._core_bpe = _tiktoken.CoreBPE(mergeable_ranks, special_tokens, pat_str)
```

**执行后得到什么：** 固定口径的编码对象。

**接下来到哪里：** 步骤 13 编码最终组装文本。

**失败与 P3 责任：** 模型空间 tokenizer 与上下文 tokenizer 是不同角色；不要复用 Embedding 的计数来代替。

<a id="s13"></a>

### 13　编码文本并获得 token ID 列表

**当前执行位置：** `Encoding.encode`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/core.py)，注释版第 98—157 行。

**收到什么：** 含来源、标题、分隔符与正文的实际待交付字符串。

**这一段怎么处理：** 处理特殊 token 允许/拒绝策略，调用 Rust 编码；遇非法 Unicode 代理项修复后再尝试。

```python
def encode(
    self,
    text: str,
    *,
    allowed_special: Literal["all"] | AbstractSet[str] = set(),  # noqa: B006
    disallowed_special: Literal["all"] | Collection[str] = "all",
) -> list[int]:
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
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        # 【中文研读】编码阶段 B：返回整数 ID 列表。要计算预算取 len(ids)，而不是把 IDs 求和；预算还应覆盖最终序列化的分隔符和来源信息。
        return self._core_bpe.encode(text, allowed_special)
```

**执行后得到什么：** 整数 ID 列表；P3 取 len(ids) 得到长度。

**接下来到哪里：** P3 超预算时按规则减少完整内容单元，重组后再次调用此方法。

**失败与 P3 责任：** 这是计算长度，不负责决定删哪条记忆；特殊标记文本如何处理需明确配置。

<a id="s14"></a>

### 14　若需要解码：先理解字符边界风险

**当前执行位置：** `Encoding.decode`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/core.py)，注释版第 314—327 行。

**收到什么：** token ID 序列。

**这一段怎么处理：** 先 decode_bytes，再按 UTF-8 及 errors 策略还原文本。

```python
def decode(self, tokens: Sequence[int], errors: str = "replace") -> str:
    # 【中文研读】处理流程：先还原字节，再按 errors 参数处理 UTF-8 解码错误。按 token 数截断后可能落在字符字节中间，P3 要检查最终文本并重新计数。
    return self._core_bpe.decode_bytes(tokens).decode("utf-8", errors=errors)
```

**执行后得到什么：** 解码文本。

**接下来到哪里：** 步骤 15 展开字节还原；返回后 P3 检查最终文本并重新计数。

**失败与 P3 责任：** 不能只对 ID 列表切片后就交付；单个汉字可能跨多个 token，裁剪也可能破坏否定或冲突语义。

<a id="s15"></a>

### 15　展开：token ID 先还原为字节

**当前执行位置：** `Encoding.decode_bytes`，完整方法或类型摘录。[出处](../第三方源码中文注释/tiktoken/core.py)，注释版第 300—309 行。

**收到什么：** 编码 ID 列表。

**这一段怎么处理：** 委托 CoreBPE 还原字节，不在此解释 UTF-8 字符。

```python
def decode_bytes(self, tokens: Sequence[int]) -> bytes:
    # 【中文研读】处理流程：委托 CoreBPE 解码，尚未按 UTF-8 转成字符串；单个 token 可能只是汉字的部分字节。
    return self._core_bpe.decode_bytes(tokens)
```

**执行后得到什么：** 字节序列。

**接下来到哪里：** 回到 decode；预算策略一般优先选择完整内容单元。

**失败与 P3 责任：** 字节解码成功与业务内容完整是不同判断。

## 本章出现的外部调用与停止展开的位置

| 调用或能力 | 在这条链中的作用 | 为什么在这里画边界 |
|---|---|---|
| BaseModel / Transformer / preprocess / self(features) | 加载模型、联合分词、前向打分 | 模型模块与底层张量计算边界；本章不展开训练和多设备内部实现 |
| batch_to_device / torch / NumPy | 张量搬运、排序索引和结果转换 | 数值辅助机制；不承担记忆授权 |
| tiktoken_ext 构造器 / _tiktoken.CoreBPE | 固定编码规则与 Rust BPE | 编码资源和底层分词边界；不是模型语义推理 |
| P3 资格核验、冲突规则、预算组包 | 决定哪些内容可以交付 | 项目领域规则，没有可冒充为上游源码的现成实现 |

## 对照 P3 应怎样使用

P3 的重排适配器必须保留每个输入候选的 memory_id + version，并把返回分数按输入索引绑定回去。CrossEncoder.rank 的 corpus_id 只是本次列表下标；它不具备跨请求业务身份。

重排启用方式由项目配置决定，默认关闭不意味着所有请求都会走本章模型链。必须重排与允许降级的异常行为要分别处理，不能把超时静默当作成功。

tokenizer 只回答“这段文本有多少 token”。材料冲突、删除、授权、超预算舍弃规则仍由 Recall 决定。实际交付文本组装完后重新计数并核验，才能返回 ContextPack。
