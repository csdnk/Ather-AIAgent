# 【中文研读】文本到 ONNX 张量的关键连接层。阅读 onnx_embed 理解真实推理，阅读 _embed_documents 理解批处理；这里的 Worker 是本地计算进程，不是 P3 持久任务系统。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
import os
from multiprocessing import get_all_start_methods
from pathlib import Path
from typing import Any, Iterable, Sequence, Type

import numpy as np
from numpy.typing import NDArray
from tokenizers import Encoding, Tokenizer

from fastembed.common.types import NumpyArray, OnnxProvider, Device
from fastembed.common.onnx_model import EmbeddingWorker, OnnxModel, OnnxOutputContext, T
from fastembed.common.preprocessor_utils import load_tokenizer
from fastembed.common.utils import iter_batch
from fastembed.parallel_processor import ParallelWorkerPool


# 【中文研读】类型职责：共用文本分词、张量构建、批处理和 token 计数能力；不规定具体模型的最终句向量算法。
class OnnxTextModel(OnnxModel[T]):
    ONNX_OUTPUT_NAMES: list[str] | None = None

    # 【中文研读】方法职责：要求具体模型选择进程池 Worker
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Type['TextEmbeddingWorker[T]']。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def _get_worker_class(cls) -> Type["TextEmbeddingWorker[T]"]:
        # 【中文研读】处理流程：缺少覆盖时抛 NotImplementedError，不自动猜测编码实现。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：要求具体模型解释图输出
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
        # 【中文研读】处理流程：默认抛 NotImplementedError；池化和归一化由子类定义。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：建立未加载的文本模型状态
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(self) -> None:
        # 【中文研读】处理流程：调用基类初始化，再把 tokenizer 设为空、特殊 token 映射设为空表。
        super().__init__()
        self.tokenizer: Tokenizer | None = None
        self.special_token_to_id: dict[str, int] = {}

    # 【中文研读】方法职责：提供输入张量调整扩展点
    # 【中文研读】输入参数：onnx_input（以图输入名称为键、张量为值的字典）；**kwargs（透传选项）。
    # 【中文研读】返回约定：dict[str, NumpyArray | NDArray[np.int64]]。结果的业务含义与失败分支见下面处理流程。
    def _preprocess_onnx_input(
        self, onnx_input: dict[str, NumpyArray], **kwargs: Any
    ) -> dict[str, NumpyArray | NDArray[np.int64]]:
        """
        Preprocess the onnx input.
        """
        # 【中文研读】处理流程：默认原样返回字典，具体模型可以覆盖以适配图结构。
        return onnx_input

    # 【中文研读】方法职责：加载图与配套 tokenizer
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

    # 【中文研读】方法职责：声明无参加载接口
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def load_onnx_model(self) -> None:
        # 【中文研读】处理流程：子类需要知道自己的目录与图文件名；基类直接报未实现。
        raise NotImplementedError("Subclasses must implement this method")

    # 【中文研读】方法职责：将一批文本分成模型 token
    # 【中文研读】输入参数：documents（待编码或待重排的正文集合）；**kwargs（透传选项）。
    # 【中文研读】返回约定：list[Encoding]。结果的业务含义与失败分支见下面处理流程。
    def tokenize(self, documents: list[str], **kwargs: Any) -> list[Encoding]:
        # 【中文研读】处理流程：使用已加载 tokenizer 的 encode_batch，返回每条文本的 IDs 和掩码；padding、截断等行为来自 tokenizer 配置。
        return self.tokenizer.encode_batch(documents)  # type: ignore[union-attr]

    # 【中文研读】方法职责：执行一批文本的真实图推理
    # 【中文研读】输入参数：documents（待编码或待重排的正文集合）；**kwargs（透传选项）。
    # 【中文研读】返回约定：OnnxOutputContext。结果的业务含义与失败分支见下面处理流程。
    def onnx_embed(
        self,
        documents: list[str],
        **kwargs: Any,
    ) -> OnnxOutputContext:
        # 【中文研读】处理流程：分词→构建 int64 输入→按图要求添加掩码和类型 IDs→模型专用预处理→Session.run→连同输入信息返回原始输出。
        # 【中文研读】阶段 1：先分词，得到 input_ids 与 attention_mask；掩码区分有效 token 与补齐位置。
        encoded = self.tokenize(documents, **kwargs)
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

        # 【中文研读】阶段 3：真正进入 ONNX Runtime，此处才产生原始向量张量。异常向调用者传播，不会变成空向量冒充成功。
        model_output = self.model.run(self.ONNX_OUTPUT_NAMES, onnx_input)  # type: ignore[union-attr]
        # 【中文研读】阶段 4：仅取图的第一个输出，并保留掩码和 token IDs，交给具体模型后处理；它还不是带 memory_id/version 的 P3 候选。
        return OnnxOutputContext(
            model_output=model_output[0],
            attention_mask=onnx_input.get("attention_mask", attention_mask),
            input_ids=onnx_input.get("input_ids", input_ids),
        )

    # 【中文研读】方法职责：按输入规模选择本进程或多进程编码
    # 【中文研读】输入参数：model_name（已注册的模型标识）；cache_dir（模型文件缓存目录）；documents（待编码或待重排的正文集合）；batch_size（每批处理的文本或文本对数量）；parallel（数据并行进程数；None 为本进程，0 表示自动取 CPU 核数）；providers（按优先级排列的 ONNX 执行后端）；cuda（GPU 使用方式：显式启用、禁用或自动选择）；device_ids（多个 Worker 可使用的设备编号）；local_files_only（只使用本地模型文件，不通过此加载路径下载）；specific_model_path（显式提供的模型文件目录）；extra_session_options（FastEmbed 对外允许的 ONNX 会话选项）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Iterable[T]。结果的业务含义与失败分支见下面处理流程。
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

    # 【中文研读】方法职责：按已加载 tokenizer 统计有效 token
    # 【中文研读】输入参数：texts（待编码或计数的文本）；batch_size（每批处理的文本或文本对数量）；**_（透传选项）。
    # 【中文研读】返回约定：int。结果的业务含义与失败分支见下面处理流程。
    def _token_count(self, texts: str | Iterable[str], batch_size: int = 1024, **_: Any) -> int:
        # 【中文研读】处理流程：必要时先加载模型和 tokenizer，文本分批编码后累加 attention_mask，padding 不计入。实际计数受 tokenizer 截断配置影响。
        if not hasattr(self, "model") or self.model is None:
            self.load_onnx_model()  # loads the tokenizer as well

        token_num = 0
        assert self.tokenizer is not None
        texts = [texts] if isinstance(texts, str) else texts
        for batch in iter_batch(texts, batch_size):
            for tokens in self.tokenizer.encode_batch(batch):
                # 【中文研读】这是当前编码模型的计数口径；P3 ContextPack 交给另一个大模型时，应使用那个模型约定的 tokenizer 重新计数。
                token_num += sum(tokens.attention_mask)

        return token_num


# 【中文研读】类型职责：本地进程池执行单元，计算并回传批次序号和 ONNX 输出。
class TextEmbeddingWorker(EmbeddingWorker[T]):
    # 【中文研读】方法职责：处理带序号的编码批次
    # 【中文研读】输入参数：items（带批次序号的输入流）。
    # 【中文研读】返回约定：Iterable[tuple[int, OnnxOutputContext]]。结果的业务含义与失败分支见下面处理流程。
    def process(self, items: Iterable[tuple[int, Any]]) -> Iterable[tuple[int, OnnxOutputContext]]:
        # 【中文研读】处理流程：逐批调用模型 onnx_embed，返回原序号与原始输出，供池恢复输入顺序；没有持久租约或跨进程重启恢复保证。
        for idx, batch in items:
            onnx_output = self.model.onnx_embed(batch)
            yield idx, onnx_output
