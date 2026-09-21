# 【中文研读】FastEmbed 到 ONNX Runtime 的装配边界：选择 CPU/GPU 后端、配置线程、创建会话。会话负责计算，P3 的 trace、截止时间、持久重试与结果提交仍由业务适配层负责。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Iterable, Sequence, Type, TypeVar

import numpy as np
import onnxruntime as ort

from numpy.typing import NDArray
from tokenizers import Tokenizer

from fastembed.common.types import OnnxProvider, NumpyArray, Device
from fastembed.parallel_processor import Worker

# Holds type of the embedding result
T = TypeVar("T")


# 【中文研读】类型职责：原始图输出与输入辅助信息，供后处理读取；不包含授权、记忆版本或任务状态。
@dataclass
class OnnxOutputContext:
    model_output: NumpyArray
    attention_mask: NDArray[np.int64] | None = None
    input_ids: NDArray[np.int64] | None = None
    metadata: dict[str, Any] | None = None


# 【中文研读】类型职责：推理会话生命周期与后端配置的共用底座；泛型 T 由具体模型决定。
class OnnxModel(Generic[T]):
    EXPOSED_SESSION_OPTIONS = ("enable_cpu_mem_arena",)

    # 【中文研读】方法职责：声明并行计算 Worker 类型
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Type['EmbeddingWorker[T]']。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def _get_worker_class(cls) -> Type["EmbeddingWorker[T]"]:
        # 【中文研读】处理流程：基类抛 NotImplementedError，必须由具体模型覆盖。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：声明图输出转换接口
    # 【中文研读】输入参数：output（推理输出及其输入掩码）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[T]。结果的业务含义与失败分支见下面处理流程。
    def _post_process_onnx_output(self, output: OnnxOutputContext, **kwargs: Any) -> Iterable[T]:
        """Post-process the ONNX model output to convert it into a usable format.

        Args:
            output (OnnxOutputContext): The raw output from the ONNX model.
            **kwargs: Additional keyword arguments that may be needed by specific implementations.

        Returns:
            Iterable[T]: Post-processed output as an iterable of type T.
        """
        # 【中文研读】处理流程：基类不假定输出结构，直接报未实现，交由具体模型定义池化和归一化。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：初始化未加载状态
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(self) -> None:
        # 【中文研读】处理流程：会话与 tokenizer 都先置空，真正文件加载在后续执行。
        self.model: ort.InferenceSession | None = None
        self.tokenizer: Tokenizer | None = None

    # 【中文研读】方法职责：提供图输入预处理扩展点
    # 【中文研读】输入参数：onnx_input（以图输入名称为键、张量为值的字典）；**kwargs（透传选项）。
    # 【中文研读】返回约定：dict[str, NumpyArray]。结果的业务含义与失败分支见下面处理流程。
    def _preprocess_onnx_input(
        self, onnx_input: dict[str, NumpyArray], **kwargs: Any
    ) -> dict[str, NumpyArray]:
        """
        Preprocess the onnx input.
        """
        # 【中文研读】处理流程：当前原样返回张量字典，不进行模型计算。
        return onnx_input

    # 【中文研读】方法职责：配置并建立真实 ONNX 推理会话
    # 【中文研读】输入参数：model_dir（模型和 tokenizer 的所在目录）；model_file（ONNX 计算图文件名称）；threads（单个推理会话线程数）；providers（按优先级排列的 ONNX 执行后端）；cuda（GPU 使用方式：显式启用、禁用或自动选择）；device_id（当前进程使用的设备编号）；extra_session_options（FastEmbed 对外允许的 ONNX 会话选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
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
        # List of Execution Providers: https://onnxruntime.ai/docs/execution-providers
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
            # check providers available
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

    # 【中文研读】方法职责：筛选此封装允许透传的会话选项
    # 【中文研读】输入参数：model_kwargs（模型加载或推理附加选项）。
    # 【中文研读】返回约定：dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def _select_exposed_session_options(cls, model_kwargs: dict[str, Any]) -> dict[str, Any]:
        """A convenience method to select the exposed session options in models

        Args:
            model_kwargs (dict[str, Any]): The model kwargs.

        Returns:
            dict[str, Any]: a dict with filtered exposed session options.
        """
        # 【中文研读】处理流程：只保留 EXPOSED_SESSION_OPTIONS 中的键；当前仅公开 enable_cpu_mem_arena，不等于任意 ORT 参数都能透传。
        return {k: v for k, v in model_kwargs.items() if k in cls.EXPOSED_SESSION_OPTIONS}

    # 【中文研读】方法职责：原地应用允许的会话配置
    # 【中文研读】输入参数：session_options；extra_options。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def add_extra_session_options(
        cls, session_options: ort.SessionOptions, extra_options: dict[str, Any]
    ) -> None:
        """Add extra session options to the existing options object in-place

        Args:
            session_options (ort.SessionOptions): The existing session options object.
            extra_options (dict[str, Any]): The extra session options available in cls.EXPOSED_SESSION_OPTIONS.

        Returns:
            None
        """
        # 【中文研读】处理流程：先断言键都在白名单，再设置 enable_cpu_mem_arena；调用者的 SessionOptions 对象因此被修改。
        for option in extra_options:
            assert (
                option in cls.EXPOSED_SESSION_OPTIONS
            ), f"{option} is unknown or not exposed (exposed options: {cls.EXPOSED_SESSION_OPTIONS})"
        if "enable_cpu_mem_arena" in extra_options:
            session_options.enable_cpu_mem_arena = extra_options["enable_cpu_mem_arena"]

    # 【中文研读】方法职责：要求模型提供绑定路径的加载实现
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def load_onnx_model(self) -> None:
        # 【中文研读】处理流程：基类抛 NotImplementedError，具体实现将目录和配置传给 _load_onnx_model。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：声明真实推理接口
    # 【中文研读】输入参数：*args（透传选项）；**kwargs（透传选项）。
    # 【中文研读】返回约定：OnnxOutputContext。结果的业务含义与失败分支见下面处理流程。
    def onnx_embed(self, *args: Any, **kwargs: Any) -> OnnxOutputContext:
        # 【中文研读】处理流程：缺少子类覆盖就报错，不提供假向量或成功占位结果。
        raise NotImplementedError("Subclasses must implement this method")


# 【中文研读】类型职责：本地并行计算 Worker 抽象，不提供 Outbox、租约或持久业务恢复。
class EmbeddingWorker(Worker, Generic[T]):
    # 【中文研读】方法职责：要求 Worker 构造具体模型实例
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；**kwargs（透传选项）。
    # 【中文研读】返回约定：OnnxModel[T]。结果的业务含义与失败分支见下面处理流程。
    def init_embedding(
        self,
        model_name: str,
        cache_dir: str,
        **kwargs: Any,
    ) -> OnnxModel[T]:
        # 【中文研读】处理流程：默认抛 NotImplementedError，具体实现指定模型类型与线程设置。
        raise NotImplementedError()

    # 【中文研读】方法职责：给 Worker 配置一个可调用的模型实例
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；**kwargs（透传选项）。
    # 【中文研读】返回约定：以方法内 return 为准；构造器负责装配对象。结果的业务含义与失败分支见下面处理流程。
    def __init__(
        self,
        model_name: str,
        cache_dir: str,
        **kwargs: Any,
    ):
        # 【中文研读】处理流程：执行 init_embedding 并保存结果，加载是否发生由具体模型的 lazy_load 决定。
        self.model = self.init_embedding(model_name, cache_dir, **kwargs)

    # 【中文研读】方法职责：供进程池启动 Worker 实例
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；**kwargs（透传选项）。
    # 【中文研读】返回约定：'EmbeddingWorker[T]'。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def start(cls, model_name: str, cache_dir: str, **kwargs: Any) -> "EmbeddingWorker[T]":
        # 【中文研读】处理流程：调用当前类构造器，传入模型名称、缓存目录和附加配置，返回该实例。
        return cls(model_name=model_name, cache_dir=cache_dir, **kwargs)

    # 【中文研读】方法职责：声明批次流处理接口
    # 【中文研读】输入参数：items（带批次序号的输入流）。
    # 【中文研读】返回约定：Iterable[tuple[int, Any]]。结果的业务含义与失败分支见下面处理流程。
    def process(self, items: Iterable[tuple[int, Any]]) -> Iterable[tuple[int, Any]]:
        # 【中文研读】处理流程：基类直接抛错；具体 Worker 要维护输入序号与输出对应关系。
        raise NotImplementedError("Subclasses must implement this method")
