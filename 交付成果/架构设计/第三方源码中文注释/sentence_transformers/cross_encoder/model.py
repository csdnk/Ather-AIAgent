# 【中文研读】Sentence Transformers 5.7.0 的 CrossEncoder 推理入口。重点注释构造、predict、rank 和输入形状识别；训练、发布和多设备内部调度不在本次逐段解读范围。P3 用其给少量合法候选重排，不用它取代向量索引。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
from __future__ import annotations

import logging
import math
import queue
from collections import OrderedDict
from collections.abc import Callable
from multiprocessing import Queue
from typing import Any, Literal, overload

import numpy as np
import torch
from torch import nn
from tqdm.autonotebook import trange
from transformers import AutoConfig, PretrainedConfig, PreTrainedModel, is_datasets_available
from transformers.utils import logging as transformers_logging
from typing_extensions import deprecated

from sentence_transformers.base.modality_types import PairableInput, PairInput
from sentence_transformers.base.model import BaseModel
from sentence_transformers.base.modules import Dense, Transformer
from sentence_transformers.cross_encoder.fit_mixin import FitMixin
from sentence_transformers.cross_encoder.model_card import CrossEncoderModelCardData
from sentence_transformers.cross_encoder.modules.logit_score import LogitScore
from sentence_transformers.util import batch_to_device, fullname, import_from_string
from sentence_transformers.util.decorators import (
    cross_encoder_init_args_decorator,
    cross_encoder_predict_rank_args_decorator,
)

# NOTE: transformers wraps the regular logging module for e.g. warning_once
logger = transformers_logging.get_logger(__name__)


# 【中文研读】类型职责：联合读取 Query 与候选正文后打分的模型容器；返回相关性分数而非可独立缓存的正文向量。
class CrossEncoder(BaseModel, FitMixin):
    """
    Loads or creates a CrossEncoder model that takes a sentence pair as input and outputs a score or label.

    A CrossEncoder does not produce sentence embeddings. Instead, it processes both sentences jointly through the
    transformer and outputs a score (regression) or class probabilities (classification). This makes it more
    accurate for pairwise tasks like reranking or semantic textual similarity, but it cannot pre-compute embeddings
    for individual sentences.

    Args:
        model_name_or_path (str, optional): If a filepath on disk, loads the model from that path. Otherwise, tries
            to download a pre-trained CrossEncoder model. If that fails, tries to construct a model from the
            Hugging Face Hub with that name. Defaults to None.
        modules (list[nn.Module], optional): A list of torch modules that are called sequentially. Can be used to
            create custom CrossEncoder models from scratch. Defaults to None.
        device (str, optional): Device (like ``"cuda"``, ``"cpu"``, ``"mps"``, ``"npu"``) that should be used for
            computation. If None, checks if a GPU can be used. If a ``device_map`` is provided via
            ``model_kwargs``, that controls device placement and this argument is ignored. Defaults to None.
        prompts (dict[str, str], optional): A dictionary with prompts for the model. The key is the prompt name,
            the value is the prompt text. The prompt text will be prepended before any text to encode. For example:
            ``{"query": "query: ", "passage": "passage: "}``. If a model has saved prompts, you can override
            them by passing your own, or pass ``{"query": "", "document": ""}`` to disable them.
            Defaults to None.
        default_prompt_name (str, optional): The name of the prompt that should be used by default. If not set,
            no prompt will be applied. Defaults to None.
        cache_folder (str, optional): Path to store models. Can also be set by the ``SENTENCE_TRANSFORMERS_HOME``
            environment variable. Defaults to None.
        trust_remote_code (bool, optional): Whether to allow for custom models defined on the Hub in their own
            modeling files. Only set to ``True`` for repositories you trust and in which you have read the code,
            as it will execute code present on the Hub on your local machine. Defaults to False.
        revision (str, optional): The specific model version to use. It can be a branch name, a tag name, or a
            commit id, for a stored model on Hugging Face. Defaults to None.
        local_files_only (bool, optional): Whether to only look at local files (i.e., do not try to download
            the model). Defaults to False.
        token (bool or str, optional): Hugging Face authentication token to download private models.
            Defaults to None.
        model_kwargs (dict[str, Any], optional): Keyword arguments passed to the underlying Hugging Face
            Transformers model via ``AutoModel.from_pretrained``. Particularly useful options include:

            - ``torch_dtype``: Override the default ``torch.dtype`` and load the model under a specific
              dtype. Can be ``torch.float16``, ``torch.bfloat16``, ``torch.float32``, or ``"auto"`` to
              use the dtype from the model's ``config.json``.
            - ``attn_implementation``: The attention implementation to use. For example ``"eager"``,
              ``"sdpa"``, or ``"flash_attention_2"``. If you ``pip install kernels``, then
              ``"flash_attention_2"`` should work without having to install ``flash_attn``. It is
              frequently the fastest option. Defaults to ``"sdpa"`` when available (torch>=2.1.1).
            - ``device_map``: Controls how the model is placed across devices, e.g. ``"auto"`` for
              model parallelism, or a single device like ``"cuda:1"`` to load the backbone directly
              onto one GPU (useful when serving multiple models in one process). When set, it takes
              precedence over the ``device`` argument.
            - ``provider``: For ``backend="onnx"``, the ONNX execution provider
              (e.g. ``"CUDAExecutionProvider"``).
            - ``file_name``: For ``backend="onnx"`` or ``"openvino"``, the filename to load
              (e.g. for optimized or quantized models).
            - ``export``: For ``backend="onnx"`` or ``"openvino"``, whether to export the model to the
              backend format. Also set automatically if the exported file doesn't exist.

            See the `PreTrainedModel.from_pretrained
            <https://huggingface.co/docs/transformers/en/main_classes/model#transformers.PreTrainedModel.from_pretrained>`_
            documentation for more details. Defaults to None.
        processor_kwargs (dict[str, Any], optional): Keyword arguments passed to the Hugging Face Transformers
            processor/tokenizer via ``AutoProcessor.from_pretrained``. See the `AutoTokenizer.from_pretrained
            <https://huggingface.co/docs/transformers/en/model_doc/auto#transformers.AutoTokenizer.from_pretrained>`_
            documentation for more details. Defaults to None.
        config_kwargs (dict[str, Any], optional): Keyword arguments passed to the Hugging Face Transformers
            config via ``AutoConfig.from_pretrained``. See the `AutoConfig.from_pretrained
            <https://huggingface.co/docs/transformers/en/model_doc/auto#transformers.AutoConfig.from_pretrained>`_
            documentation for more details. For example, you can set ``classifier_dropout`` or ``num_labels``
            via this parameter. Defaults to None.
        model_card_data (:class:`~sentence_transformers.cross_encoder.model_card.CrossEncoderModelCardData`, optional):
            A model card data object that contains information about the model. Used to generate a model card
            when saving the model. If not set, a default model card data object is created. Defaults to None.
        backend (str, optional): The backend to use for inference. Can be ``"torch"`` (default), ``"onnx"``,
            or ``"openvino"``. Defaults to ``"torch"``.
        num_labels (int, optional): Number of labels of the classifier. If 1, the CrossEncoder is a regression
            model that outputs a continuous score. If > 1, it outputs several scores that can be soft-maxed to
            get probability scores for the different classes. Defaults to None.
        max_length (int, optional): Max length for input sequences. Longer sequences will be truncated. If None,
            the max length of the model will be used. Defaults to None.
        activation_fn (Callable, optional): Activation function applied on top of the model's logits during
            :meth:`predict`. If None, ``nn.Sigmoid()`` is used when ``num_labels=1``, else ``nn.Identity()``.
            Defaults to None.

    Example:
        ::

            from sentence_transformers import CrossEncoder

            # Load a pre-trained CrossEncoder model
            model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2")

            # Predict scores for sentence pairs
            pairs = [
                ("How many people live in Berlin?", "Berlin had a population of 3,520,031 in 2019."),
                ("How many people live in Berlin?", "Berlin is well known for its museums."),
            ]
            scores = model.predict(pairs)
            print(scores)
            # [8.607  1.133]

            # Rank documents by relevance to a query
            results = model.rank(
                "How many people live in Berlin?",
                ["Berlin had a population of 3,520,031 in 2019.", "Berlin is well known for its museums."],
            )
            print(results)
            # [{'corpus_id': 0, 'score': 8.607317}, {'corpus_id': 1, 'score': 1.1329174}]
    """

    model_card_data_class = CrossEncoderModelCardData
    default_huggingface_organization: str | None = "cross-encoder"
    _model_card_model_id_placeholder = "cross_encoder_model_id"
    model_type: str = "CrossEncoder"

    # 【中文研读】方法职责：装配联合输入打分模型
    # 【中文研读】输入参数：model_name_or_path（模型仓库名称或本地模型目录）；modules；device（推理使用的 CPU 或 GPU 设备）；prompts；default_prompt_name；cache_folder（模型文件缓存目录）；trust_remote_code（是否允许加载模型仓库的自定义代码）；revision（模型仓库版本）；local_files_only（只使用本地模型文件，不通过此加载路径下载）；token（连接凭证）；model_kwargs（模型加载或推理附加选项）；processor_kwargs（分词与输入处理组件的加载选项）；config_kwargs（模型配置覆盖项）；model_card_data；backend（底层模型后端）；num_labels（每对输入的输出标签数）；max_length（联合输入的模型长度限制）；activation_fn（把模型原始输出变换为最终分数的函数）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
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
        # CrossEncoder-specific args
        num_labels: int | None = None,
        max_length: int | None = None,
        activation_fn: Callable | None = None,
    ) -> None:
        # Set before super().__init__() so _parse_model_config can check these
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

        # If an activation function is provided, use it. Otherwise, load the default one/from backwards compatibility
        # if it wasn't set during super().__init__()
        if activation_fn is not None:
            self.activation_fn = activation_fn
        elif self.activation_fn is None:
            self.activation_fn = self.get_default_activation_fn()

    # 【中文研读】方法职责：按模型配置构建打分模块
    # 【中文研读】输入参数：model_name_or_path（模型仓库名称或本地模型目录）；token（连接凭证）；cache_folder（模型文件缓存目录）；revision（模型仓库版本）；trust_remote_code（是否允许加载模型仓库的自定义代码）；local_files_only（只使用本地模型文件，不通过此加载路径下载）；model_kwargs（模型加载或推理附加选项）；processor_kwargs（分词与输入处理组件的加载选项）；config_kwargs（模型配置覆盖项）。
    # 【中文研读】返回约定：tuple[list[nn.Module] | OrderedDict[str, nn.Module], dict[str, Any]]。结果的业务含义与失败分支见下面处理流程。
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

        # Otherwise, assume sequence-classification
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

    def _multi_process(
        self,
        inputs: list[PairInput],
        show_progress_bar: bool | None = True,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        device: str | list[str | torch.device] | None = None,
        chunk_size: int | None = None,
        **predict_kwargs,
    ):
        convert_to_tensor = predict_kwargs.get("convert_to_tensor", False)
        convert_to_numpy = predict_kwargs.get("convert_to_numpy", True)
        predict_kwargs["show_progress_bar"] = False

        created_pool = False
        if pool is None and isinstance(device, list) and len(device) > 0:
            pool = self.start_multi_process_pool(device)
            created_pool = True

        # Create a pool if is not provided, but a list of devices is
        try:
            # Determine chunk size if not provided. As a default, aim for 10 chunks per process, with a maximum of 5000 sentences per chunk.
            if chunk_size is None:
                chunk_size = min(math.ceil(len(inputs) / len(pool["processes"]) / 10), 5000)
                chunk_size = max(chunk_size, 1)  # Ensure at least 1

            input_queue: torch.multiprocessing.Queue = pool["input"]
            output_queue: torch.multiprocessing.Queue = pool["output"]

            # Send inputs to the input queue in chunks
            chunk_id = -1  # We default to -1 to handle empty input gracefully
            for chunk_id, chunk_start in enumerate(range(0, len(inputs), chunk_size)):
                chunk = inputs[chunk_start : chunk_start + chunk_size]
                input_queue.put([chunk_id, chunk, predict_kwargs])

            # Collect results from output queue
            output_list = sorted(
                [output_queue.get() for _ in trange(chunk_id + 1, desc="Chunks", disable=not show_progress_bar)],
                key=lambda x: x[0],  # Sort by chunk_id
            )

            # Handle the various output formats: torch tensors, numpy arrays, or
            # list of dictionaries, also when empty.
            scores = [output[1] for output in output_list]

            # Check for errors in results
            if any(len(output) > 2 and output[2] is not None for output in output_list):
                # Error occurred in worker
                error_output = next(output for output in output_list if len(output) > 2 and output[2])
                raise RuntimeError(f"Error in worker process: {error_output[2]}")

            if scores:
                if isinstance(scores[0], torch.Tensor):
                    scores = torch.cat(scores)
                elif isinstance(scores[0], np.ndarray):
                    scores = np.concatenate(scores, axis=0)
                elif isinstance(scores[0], list):
                    scores = sum(scores, [])
                else:
                    scores = sum(scores, [])

            elif convert_to_tensor:
                scores = torch.tensor([], device=self.device)
            elif convert_to_numpy:
                scores = np.array([])
            else:
                scores = []
            return scores

        finally:
            # Clean up the pool if we created it
            if created_pool:
                self.stop_multi_process_pool(pool)

    @staticmethod
    def _multi_process_worker(
        target_device: str,
        model: CrossEncoder,
        input_queue: Queue,
        results_queue: Queue,
    ) -> None:
        """
        Internal working process to predict input pairs in a multi-process setup.

        """
        while True:
            chunk_id = None
            try:
                chunk_id, sentence_pairs, kwargs = input_queue.get()
                scores = model.predict(sentence_pairs, device=target_device, **kwargs)

                # If multi-process scores are not on CPUs, move them to CPU, so they can all be concatenated later
                if isinstance(scores, torch.Tensor) and scores.device.type != "cpu":
                    scores = scores.cpu()
                elif isinstance(scores, np.ndarray):
                    scores = np.asarray(scores)
                elif isinstance(scores, list):
                    scores = [
                        score.cpu() if isinstance(score, torch.Tensor) and score.device.type != "cpu" else score
                        for score in scores
                    ]
                results_queue.put([chunk_id, scores])

            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"Error in worker process on {target_device}: {e}")
                try:
                    results_queue.put([chunk_id, None, str(e)])
                except Exception:
                    pass
                break

    def _resolve_activation_fn(self, activation_fn_path: str) -> Callable | None:
        """Instantiate an activation function from a dotted path string, respecting trust_remote_code."""
        if self.trust_remote_code or activation_fn_path.startswith("torch."):
            return import_from_string(activation_fn_path)()
        logger.warning(
            f"Activation function path '{activation_fn_path}' is not trusted, using default activation function instead. "
            "Please load the CrossEncoder with `trust_remote_code=True` to allow loading custom activation "
            "functions via the configuration."
        )
        return None

    def get_default_activation_fn(self) -> Callable:
        activation_fn_path = None
        config = self.config
        if hasattr(config, "sentence_transformers") and "activation_fn" in config.sentence_transformers:
            activation_fn_path = config.sentence_transformers["activation_fn"]

        # Backwards compatibility with <v4.0: we stored the activation_fn under 'sbert_ce_default_activation_function'
        elif (
            hasattr(config, "sbert_ce_default_activation_function")
            and config.sbert_ce_default_activation_function is not None
        ):
            activation_fn_path = config.sbert_ce_default_activation_function
            del config.sbert_ce_default_activation_function

        if activation_fn_path is not None:
            resolved = self._resolve_activation_fn(activation_fn_path)
            if resolved is not None:
                return resolved

        if self.num_labels == 1:
            return nn.Sigmoid()
        return nn.Identity()

    @property
    def model(self) -> PreTrainedModel | None:
        return self.transformers_model

    @property
    def num_labels(self) -> int:
        for module in reversed(self):
            if isinstance(module, Dense) and module.module_output_name == "scores":
                return module.out_features
            if isinstance(module, Transformer):
                return module.model.config.num_labels
            if isinstance(module, LogitScore):
                return 1
        # Default to 1, not commonly reached
        return 1

    def __setattr__(self, name: str, value: Any) -> None:
        # We don't want activation_fn to be registered as a module, instead we want it as a normal attribute
        # This avoids issues with saving/loading the model
        if name == "activation_fn":
            return super(torch.nn.Module, self).__setattr__(name, value)
        return super().__setattr__(name, value)

    @property
    @deprecated("The `max_length` property was renamed and is now deprecated. Please use `max_seq_length` instead.")
    def max_length(self) -> int:
        return self.max_seq_length

    @max_length.setter
    @deprecated("The `max_length` property was renamed and is now deprecated. Please use `max_seq_length` instead.")
    def max_length(self, value: int) -> None:
        self.max_seq_length = value

    @property
    @deprecated(
        "The `default_activation_function` property was renamed and is now deprecated. "
        "Please use `activation_fn` instead."
    )
    def default_activation_function(self) -> Callable:
        return self.activation_fn

    # 【中文研读】方法职责：为查询正文对计算分数，并保持与原输入对应
    # 【中文研读】输入参数：inputs（查询与正文组成的文本对，或多对输入）；prompt_name（使用模型内预设提示的名称）；prompt（搜索提示模板）；batch_size（每批处理的文本或文本对数量）；show_progress_bar（是否展示本地批处理进度）；activation_fn（把模型原始输出变换为最终分数的函数）；apply_softmax（是否对多维输出进行 softmax）；convert_to_numpy（是否把分数转为 CPU 上的 NumPy 数组）；convert_to_tensor（是否把分数合成一个 Tensor，优先于 NumPy 选项）；device（推理使用的 CPU 或 GPU 设备）；pool（已启动的多进程推理池）；chunk_size（多进程分发的分块大小）；**kwargs（透传选项）。
    # 【中文研读】返回约定：torch.Tensor。结果的业务含义与失败分支见下面处理流程。
    @overload
    def predict(
        self,
        inputs: PairInput,
        prompt_name: str | None = ...,
        prompt: str | None = ...,
        batch_size: int = ...,
        show_progress_bar: bool | None = ...,
        activation_fn: Callable | None = ...,
        apply_softmax: bool | None = ...,
        convert_to_numpy: Literal[False] = ...,
        convert_to_tensor: Literal[False] = ...,
        device: str | list[str | torch.device] | None = None,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        chunk_size: int | None = None,
        **kwargs,
                       # 【中文研读】处理流程：以下同名 overload 是静态类型声明，真正执行在带 inference_mode 的方法中；校验批大小，规范化输入，选择设备路径，按长度组批→联合预处理→前向计算→激活与形状调整→恢复原顺序→转换结果类型。错误向调用方传播。
    ) -> torch.Tensor: ...

    # 【中文研读】方法职责：为查询正文对计算分数，并保持与原输入对应
    # 【中文研读】输入参数：inputs（查询与正文组成的文本对，或多对输入）；prompt_name（使用模型内预设提示的名称）；prompt（搜索提示模板）；batch_size（每批处理的文本或文本对数量）；show_progress_bar（是否展示本地批处理进度）；activation_fn（把模型原始输出变换为最终分数的函数）；apply_softmax（是否对多维输出进行 softmax）；convert_to_numpy（是否把分数转为 CPU 上的 NumPy 数组）；convert_to_tensor（是否把分数合成一个 Tensor，优先于 NumPy 选项）；device（推理使用的 CPU 或 GPU 设备）；pool（已启动的多进程推理池）；chunk_size（多进程分发的分块大小）；**kwargs（透传选项）。
    # 【中文研读】返回约定：np.ndarray。结果的业务含义与失败分支见下面处理流程。
    @overload
    def predict(
        self,
        inputs: list[PairInput] | PairInput,
        prompt_name: str | None = ...,
        prompt: str | None = ...,
        batch_size: int = ...,
        show_progress_bar: bool | None = ...,
        activation_fn: Callable | None = ...,
        apply_softmax: bool | None = ...,
        convert_to_numpy: Literal[True] = True,
        convert_to_tensor: Literal[False] = False,
        device: str | list[str | torch.device] | None = None,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        chunk_size: int | None = None,
        **kwargs,
                     # 【中文研读】处理流程：以下同名 overload 是静态类型声明，真正执行在带 inference_mode 的方法中；校验批大小，规范化输入，选择设备路径，按长度组批→联合预处理→前向计算→激活与形状调整→恢复原顺序→转换结果类型。错误向调用方传播。
    ) -> np.ndarray: ...

    # 【中文研读】方法职责：为查询正文对计算分数，并保持与原输入对应
    # 【中文研读】输入参数：inputs（查询与正文组成的文本对，或多对输入）；prompt_name（使用模型内预设提示的名称）；prompt（搜索提示模板）；batch_size（每批处理的文本或文本对数量）；show_progress_bar（是否展示本地批处理进度）；activation_fn（把模型原始输出变换为最终分数的函数）；apply_softmax（是否对多维输出进行 softmax）；convert_to_numpy（是否把分数转为 CPU 上的 NumPy 数组）；convert_to_tensor（是否把分数合成一个 Tensor，优先于 NumPy 选项）；device（推理使用的 CPU 或 GPU 设备）；pool（已启动的多进程推理池）；chunk_size（多进程分发的分块大小）；**kwargs（透传选项）。
    # 【中文研读】返回约定：torch.Tensor。结果的业务含义与失败分支见下面处理流程。
    @overload
    def predict(
        self,
        inputs: list[PairInput] | PairInput,
        prompt_name: str | None = ...,
        prompt: str | None = ...,
        batch_size: int = ...,
        show_progress_bar: bool | None = ...,
        activation_fn: Callable | None = ...,
        apply_softmax: bool | None = ...,
        convert_to_numpy: bool = ...,
        convert_to_tensor: Literal[True] = ...,
        device: str | list[str | torch.device] | None = None,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        chunk_size: int | None = None,
        **kwargs,
                       # 【中文研读】处理流程：以下同名 overload 是静态类型声明，真正执行在带 inference_mode 的方法中；校验批大小，规范化输入，选择设备路径，按长度组批→联合预处理→前向计算→激活与形状调整→恢复原顺序→转换结果类型。错误向调用方传播。
    ) -> torch.Tensor: ...

    # 【中文研读】方法职责：为查询正文对计算分数，并保持与原输入对应
    # 【中文研读】输入参数：inputs（查询与正文组成的文本对，或多对输入）；prompt_name（使用模型内预设提示的名称）；prompt（搜索提示模板）；batch_size（每批处理的文本或文本对数量）；show_progress_bar（是否展示本地批处理进度）；activation_fn（把模型原始输出变换为最终分数的函数）；apply_softmax（是否对多维输出进行 softmax）；convert_to_numpy（是否把分数转为 CPU 上的 NumPy 数组）；convert_to_tensor（是否把分数合成一个 Tensor，优先于 NumPy 选项）；device（推理使用的 CPU 或 GPU 设备）；pool（已启动的多进程推理池）；chunk_size（多进程分发的分块大小）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[torch.Tensor]。结果的业务含义与失败分支见下面处理流程。
    @overload
    def predict(
        self,
        inputs: list[PairInput],
        prompt_name: str | None = ...,
        prompt: str | None = ...,
        batch_size: int = ...,
        show_progress_bar: bool | None = ...,
        activation_fn: Callable | None = ...,
        apply_softmax: bool | None = ...,
        convert_to_numpy: Literal[False] = ...,
        convert_to_tensor: Literal[False] = ...,
        device: str | list[str | torch.device] | None = None,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        chunk_size: int | None = None,
        **kwargs,
                             # 【中文研读】处理流程：以下同名 overload 是静态类型声明，真正执行在带 inference_mode 的方法中；校验批大小，规范化输入，选择设备路径，按长度组批→联合预处理→前向计算→激活与形状调整→恢复原顺序→转换结果类型。错误向调用方传播。
    ) -> list[torch.Tensor]: ...

    # 【中文研读】方法职责：为查询正文对计算分数，并保持与原输入对应
    # 【中文研读】输入参数：inputs（查询与正文组成的文本对，或多对输入）；prompt_name（使用模型内预设提示的名称）；prompt（搜索提示模板）；batch_size（每批处理的文本或文本对数量）；show_progress_bar（是否展示本地批处理进度）；activation_fn（把模型原始输出变换为最终分数的函数）；apply_softmax（是否对多维输出进行 softmax）；convert_to_numpy（是否把分数转为 CPU 上的 NumPy 数组）；convert_to_tensor（是否把分数合成一个 Tensor，优先于 NumPy 选项）；device（推理使用的 CPU 或 GPU 设备）；pool（已启动的多进程推理池）；chunk_size（多进程分发的分块大小）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[torch.Tensor] | np.ndarray | torch.Tensor。结果的业务含义与失败分支见下面处理流程。
    @torch.inference_mode()
    @cross_encoder_predict_rank_args_decorator
    def predict(
        self,
        inputs: list[PairInput] | PairInput,
        prompt_name: str | None = None,
        prompt: str | None = None,
        batch_size: int = 32,
        show_progress_bar: bool | None = None,
        activation_fn: Callable | None = None,
        apply_softmax: bool | None = False,
        convert_to_numpy: bool = True,
        convert_to_tensor: bool = False,
        device: str | list[str | torch.device] | None = None,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        chunk_size: int | None = None,
        **kwargs,
    ) -> list[torch.Tensor] | np.ndarray | torch.Tensor:
        """
        Performs predictions with the CrossEncoder on the given input pairs.

        .. tip::

            Adjusting ``batch_size`` can significantly improve processing speed. The optimal value depends on your
            hardware, model size, precision, and input length. Benchmark a few batch sizes on a small subset of your
            data to find the best value.

        Args:
            inputs (Union[List[PairInput], PairInput]): A list of input pairs or one input pair, where each element
                can be a string, image, or multimodal dict.
            prompt_name (Optional[str], optional): The name of the prompt to use for encoding.
            prompt (Optional[str], optional): The prompt to use for encoding.
            batch_size (int, optional): Batch size for encoding. Defaults to 32.
            show_progress_bar (bool, optional): Output progress bar. Defaults to None.
            activation_fn (callable, optional): Activation function applied on the logits output of the CrossEncoder.
                If None, the ``model.activation_fn`` will be used, which defaults to :class:`torch.nn.Sigmoid` if num_labels=1, else
                :class:`torch.nn.Identity`. Defaults to None.
            apply_softmax (bool, optional): If set to True and `model.num_labels > 1`, applies softmax on the logits
                output such that for each sample, the scores of each class sum to 1. Defaults to False.
            convert_to_numpy (bool, optional): Whether the output should be a list of numpy vectors. If False, output
                a list of PyTorch tensors. Defaults to True.
            convert_to_tensor (bool, optional): Whether the output should be one large tensor. Overwrites `convert_to_numpy`.
                Defaults to False.
            device (Union[str, List[str]], optional): Device(s) to use for computation. Can be a single device string
                (e.g., "cuda:0", "cpu") or a list of devices (e.g., ["cuda:0", "cuda:1"]). If a list is provided,
                multiprocessing will be used automatically. Defaults to None.
            pool (Dict[str, Any], optional): A pool of workers created with :meth:`start_multi_process_pool`. If provided,
                multiprocessing will be used. If None and ``device`` is a list, a pool will be created automatically.
                Defaults to None.
            chunk_size (int, optional): Size of chunks for multiprocessing. If None, a sensible default is calculated.
                Only used when ``pool`` is not None or ``device`` is a list. Defaults to None.
            **kwargs: Additional keyword arguments forwarded to the model's ``preprocess`` and ``forward``. A notable
                one is ``processing_kwargs``, which lets you override the processor kwargs configured on the
                underlying :class:`~sentence_transformers.base.modules.Transformer` for this call only (e.g.
                ``processing_kwargs={"text": {"max_length": 256, "truncation": True}}``). See
                :meth:`Transformer.preprocess <sentence_transformers.base.modules.Transformer.preprocess>` for the
                accepted structure.

        Returns:
            Union[List[torch.Tensor], np.ndarray, torch.Tensor]: Predictions for the passed input pairs.
            The return type depends on the ``convert_to_numpy`` and ``convert_to_tensor`` parameters.
            If ``convert_to_tensor`` is True, the output will be a :class:`torch.Tensor`.
            If ``convert_to_numpy`` is True, the output will be a :class:`numpy.ndarray`.
            Otherwise, the output will be a list of :class:`torch.Tensor` values.

        Examples:
            ::

                from sentence_transformers import CrossEncoder

                model = CrossEncoder("cross-encoder/stsb-roberta-base")
                sentences = [["I love cats", "Cats are amazing"], ["I prefer dogs", "Dogs are loyal"]]
                model.predict(sentences)
                # => array([0.6912767, 0.4303499], dtype=float32)

                # Using multiprocessing with automatic pool
                scores = model.predict(sentences, device=["cuda:0", "cuda:1"])

                # Using multiprocessing with manual pool
                pool = model.start_multi_process_pool()
                scores = model.predict(sentences, pool=pool)
                model.stop_multi_process_pool(pool)
        """
        # 【中文研读】处理流程：以下同名 overload 是静态类型声明，真正执行在带 inference_mode 的方法中；校验批大小，规范化输入，选择设备路径，按长度组批→联合预处理→前向计算→激活与形状调整→恢复原顺序→转换结果类型。错误向调用方传播。
        if show_progress_bar is None:
            show_progress_bar = (
                logger.getEffectiveLevel() == logging.INFO or logger.getEffectiveLevel() == logging.DEBUG
            )

        # 【中文研读】阶段 1：尽早拒绝无效批大小，不能把没有执行的批次记成成功。
        if batch_size <= 0:
            raise ValueError(f"batch_size must be a positive integer, got {batch_size}.")

        # Cast an individual pair to a list with length 1
        # 【中文研读】阶段 2：把单个 [query,doc] 规范化成一批，后面所有处理使用一致的批次接口。
        is_singular_input = self.is_singular_input(inputs)
        if is_singular_input:
            # A 1D numpy string array is a single pair; convert to a list so downstream sees ["q", "d"].
            if isinstance(inputs, np.ndarray):
                inputs = inputs.tolist()
            inputs = [inputs]
        elif not isinstance(inputs, list):
            # Materialize e.g. datasets.Column to avoid slow Arrow deserialization on each index
            inputs = inputs.tolist() if isinstance(inputs, np.ndarray) else list(inputs)

        # If pool or a list of devices is provided, use multi-process prediction
        # 【中文研读】可选分支：多设备/外部池进入 _multi_process。本次研读主线是单设备分批推理，持久任务并发仍由 P3 控制。
        if pool is not None or (isinstance(device, list) and len(device) > 0):
            pred_scores = self._multi_process(
                inputs=inputs,
                # Utility and post-processing parameters
                show_progress_bar=show_progress_bar,
                # Multi-process encoding parameters
                pool=pool,
                device=device,
                chunk_size=chunk_size,
                # Prediction parameters
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

        prompt = self._resolve_prompt(prompt, prompt_name)

        # Here, device is either a single device string (e.g., "cuda:0", "cpu") for single-process encoding or None
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
            # Route through __call__ so that model.compile() applies to the forward pass.
            # 【中文研读】阶段 6：真正模型前向计算，按模块链返回 scores；此处不访问 Milvus，也不生成给用户的自然语言回答。
            out_features = self(features, **kwargs)
            scores = out_features["scores"]

            if activation_fn is not None:
                # 【中文研读】分数可能经过 sigmoid 或其他映射；不能仅因取值在 0～1 就当作业务正确概率，不同模型分数阈值不能直接通用。
                scores = activation_fn(scores)

            if apply_softmax and scores.ndim > 1:
                scores = torch.nn.functional.softmax(scores, dim=1)

            # Squeeze [batch_size, 1] -> [batch_size] for single-label models
            if num_labels == 1 and scores.ndim > 1:
                scores = scores.squeeze(-1)

            pred_scores.extend(scores)

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

    # 【中文研读】方法职责：把一个查询与多条候选组成文本对并按分数排序
    # 【中文研读】输入参数：query（查询文本或查询对象，见类型签名）；documents（待编码或待重排的正文集合）；top_k（最多保留的排序结果数）；return_documents（是否在排序结果中附加原文）；prompt_name（使用模型内预设提示的名称）；prompt（搜索提示模板）；batch_size（每批处理的文本或文本对数量）；show_progress_bar（是否展示本地批处理进度）；activation_fn（把模型原始输出变换为最终分数的函数）；apply_softmax（是否对多维输出进行 softmax）；convert_to_numpy（是否把分数转为 CPU 上的 NumPy 数组）；convert_to_tensor（是否把分数合成一个 Tensor，优先于 NumPy 选项）；device（推理使用的 CPU 或 GPU 设备）；pool（已启动的多进程推理池）；chunk_size（多进程分发的分块大小）。
    # 【中文研读】返回约定：list[dict[Literal['corpus_id', 'score', 'text'], int | float | str]]。结果的业务含义与失败分支见下面处理流程。
    @cross_encoder_predict_rank_args_decorator
    def rank(
        self,
        query: PairableInput,
        documents: list[PairableInput],
        top_k: int | None = None,
        return_documents: bool = False,
        prompt_name: str | None = None,
        prompt: str | None = None,
        batch_size: int = 32,
        show_progress_bar: bool | None = None,
        activation_fn: Callable | None = None,
        apply_softmax=False,
        convert_to_numpy: bool = True,
        convert_to_tensor: bool = False,
        device: str | list[str | torch.device] | None = None,
        pool: dict[Literal["input", "output", "processes"], Any] | None = None,
        chunk_size: int | None = None,
    ) -> list[dict[Literal["corpus_id", "score", "text"], int | float | str]]:
        """
        Performs ranking with the CrossEncoder on the given query and documents. Returns a sorted list with the document indices and scores.

        .. tip::

            Adjusting ``batch_size`` can significantly improve processing speed. The optimal value depends on your
            hardware, model size, precision, and input length. Benchmark a few batch sizes on a small subset of your
            data to find the best value.

        Args:
            query (PairableInput): A single query, e.g. a string, image, or multimodal dict.
            documents (List[PairableInput]): A list of documents, e.g. strings, images, or multimodal dicts.
            top_k (Optional[int], optional): Return the top-k documents. If None, all documents are returned. Defaults to None.
            return_documents (bool, optional): If True, also returns the documents. If False, only returns the indices and scores. Defaults to False.
            prompt_name (Optional[str], optional): The name of the prompt to use for encoding.
            prompt (Optional[str], optional): The prompt to use for encoding.
            batch_size (int, optional): Batch size for encoding. Defaults to 32.
            show_progress_bar (bool, optional): Output progress bar. Defaults to None.
            activation_fn ([type], optional): Activation function applied on the logits output of the CrossEncoder. If None, nn.Sigmoid() will be used if num_labels=1, else nn.Identity. Defaults to None.
            convert_to_numpy (bool, optional): Convert the output to a numpy matrix. Defaults to True.
            apply_softmax (bool, optional): If there are more than 2 dimensions and apply_softmax=True, applies softmax on the logits output. Defaults to False.
            convert_to_tensor (bool, optional): Convert the output to a tensor. Defaults to False.
            device (Union[str, List[str]], optional): Device(s) to use for computation. Can be a single device string
                (e.g., "cuda:0", "cpu") or a list of devices (e.g., ["cuda:0", "cuda:1"]). If a list is provided,
                multiprocessing will be used automatically. Defaults to None.
            pool (Dict[str, Any], optional): A pool of workers created with :meth:`start_multi_process_pool`. If provided,
                multiprocessing will be used. If None and ``device`` is a list, a pool will be created automatically.
                Defaults to None.
            chunk_size (int, optional): Size of chunks for multiprocessing. If None, a sensible default is calculated.
                Only used when ``pool`` is not None or ``device`` is a list. Defaults to None.

        Returns:
            List[Dict[Literal["corpus_id", "score", "text"], Union[int, float, str]]]: A sorted list with the "corpus_id", "score", and optionally "text" of the documents.

        Example:
            ::

                from sentence_transformers import CrossEncoder
                model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2")

                query = "Who wrote 'To Kill a Mockingbird'?"
                documents = [
                    "'To Kill a Mockingbird' is a novel by Harper Lee published in 1960. It was immediately successful, winning the Pulitzer Prize, and has become a classic of modern American literature.",
                    "The novel 'Moby-Dick' was written by Herman Melville and first published in 1851. It is considered a masterpiece of American literature and deals with complex themes of obsession, revenge, and the conflict between good and evil.",
                    "Harper Lee, an American novelist widely known for her novel 'To Kill a Mockingbird', was born in 1926 in Monroeville, Alabama. She received the Pulitzer Prize for Fiction in 1961.",
                    "Jane Austen was an English novelist known primarily for her six major novels, which interpret, critique and comment upon the British landed gentry at the end of the 18th century.",
                    "The 'Harry Potter' series, which consists of seven fantasy novels written by British author J.K. Rowling, is among the most popular and critically acclaimed books of the modern era.",
                    "'The Great Gatsby', a novel written by American author F. Scott Fitzgerald, was published in 1925. The story is set in the Jazz Age and follows the life of millionaire Jay Gatsby and his pursuit of Daisy Buchanan."
                ]

                model.rank(query, documents, return_documents=True)

            ::

                [{'corpus_id': 0,
                'score': 10.67858,
                'text': "'To Kill a Mockingbird' is a novel by Harper Lee published in 1960. It was immediately successful, winning the Pulitzer Prize, and has become a classic of modern American literature."},
                {'corpus_id': 2,
                'score': 9.761677,
                'text': "Harper Lee, an American novelist widely known for her novel 'To Kill a Mockingbird', was born in 1926 in Monroeville, Alabama. She received the Pulitzer Prize for Fiction in 1961."},
                {'corpus_id': 1,
                'score': -3.3099542,
                'text': "The novel 'Moby-Dick' was written by Herman Melville and first published in 1851. It is considered a masterpiece of American literature and deals with complex themes of obsession, revenge, and the conflict between good and evil."},
                {'corpus_id': 5,
                'score': -4.8989105,
                'text': "'The Great Gatsby', a novel written by American author F. Scott Fitzgerald, was published in 1925. The story is set in the Jazz Age and follows the life of millionaire Jay Gatsby and his pursuit of Daisy Buchanan."},
                {'corpus_id': 4,
                'score': -5.082967,
                'text': "The 'Harry Potter' series, which consists of seven fantasy novels written by British author J.K. Rowling, is among the most popular and critically acclaimed books of the modern era."}]
        """
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

        results = []
        for i, score in enumerate(scores):
            # TODO v6: convert score to float(score) for cleaner output
            results.append({"corpus_id": i, "score": score})
            if return_documents:
                results[-1].update({"text": documents[i]})

        # 【中文研读】最后按分数排序；P3 仍需规定同分规则、重排超时策略，并在交付 ContextPack 前再次核验当前版本和删除状态。
        results = sorted(results, key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # 【中文研读】方法职责：区分一对输入与多对输入
    # 【中文研读】输入参数：inputs（查询与正文组成的文本对，或多对输入）。
    # 【中文研读】返回约定：bool。结果的业务含义与失败分支见下面处理流程。
    def is_singular_input(self, inputs: PairInput | list[PairInput]) -> bool:
        """
        Check if the input represents a single example or a batch of examples.

        Args:
            inputs: The input to check.
        Returns:
            bool: True if the input is a single example, False if it is a batch.
        """
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
        # Numpy string/object arrays: 1D is a single pair, 2D+ is a batch, empty is an empty batch
        if isinstance(inputs, np.ndarray) and inputs.dtype.kind in ("U", "O"):
            if inputs.size == 0:
                return False
            return inputs.ndim < 2
        return True

    def _get_model_config(self) -> dict[str, Any]:
        return super()._get_model_config() | {
            "activation_fn": fullname(self.activation_fn),
        }

    def _parse_model_config(self, model_config: dict[str, Any]) -> None:
        super()._parse_model_config(model_config)
        if "activation_fn" in model_config:
            activation_fn_path = model_config["activation_fn"]
            if activation_fn_path is not None:
                resolved = self._resolve_activation_fn(activation_fn_path)
                if resolved is not None:
                    self.activation_fn = resolved

    def _push_to_hub_usage_tip(self, repo_id: str) -> str:
        class_name = self.__class__.__name__
        backend = self.get_backend()
        return f"""\
## Testing this pull request
You can test this pull request before merging by loading the model from this PR with the `revision` argument:
```python
from sentence_transformers import {class_name}

# NOTE: Update this to the number of your pull request
pr_number = 2
model = {class_name}(
    "{repo_id}",
    revision=f"refs/pr/{{pr_number}}",
    backend="{backend}",
)

# Verify that everything works as expected
scores = model.predict([("The weather is lovely today.", "It's so sunny outside!")])
print(scores)

rankings = model.rank("The weather is lovely today.", ["It's so sunny outside!", "He drove to the stadium."])
print(rankings)
```

---
*This PR was auto-generated with \
[`push_to_hub`](https://sbert.net/docs/package_reference/cross_encoder/cross_encoder.html#sentence_transformers.cross_encoder.CrossEncoder.push_to_hub).*
"""
