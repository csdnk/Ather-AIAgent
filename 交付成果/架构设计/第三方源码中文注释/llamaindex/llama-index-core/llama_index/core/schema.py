# 【中文研读】阅读顺序：QueryBundle（问题）→ TextNode/BaseNode（材料与元数据）→ NodeWithScore（候选与分数）→ IndexNode（可递归引用）→ Document（来源文档）。序列化和多模态是辅助路径，不是 P3 的业务授权模型。
# 【中文研读】中文注释为项目研读补充；英文原文、提示词和执行代码保持不变。来源版本、采用边界与阅读顺序见本目录 README.md。
"""Base schema for data structures."""

from __future__ import annotations

import base64
import json
import logging
import pickle
import textwrap
import uuid
from abc import abstractmethod
from binascii import Error as BinasciiError
from dataclasses import dataclass
from enum import Enum, auto
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Union,
)

import filetype
import requests
from dataclasses_json import DataClassJsonMixin
from deprecated import deprecated
from typing_extensions import Self
from PIL import Image

from llama_index.core.bridge.pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    JsonSchemaValue,
    PlainSerializer,
    SerializationInfo,
    SerializeAsAny,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_serializer,
)
from llama_index.core.bridge.pydantic_core import CoreSchema
from llama_index.core.instrumentation import DispatcherSpanMixin
from llama_index.core.utils import SAMPLE_TEXT, truncate_text

if TYPE_CHECKING:  # pragma: no cover
    from haystack.schema import Document as HaystackDocument  # type: ignore
    from llama_cloud.types.cloud_document import CloudDocument  # type: ignore
    from semantic_kernel.memory.memory_record import MemoryRecord  # type: ignore

    from llama_index.core.base.llms.types import BaseContentBlock
    from llama_index.core.bridge.langchain import Document as LCDocument  # type: ignore


DEFAULT_TEXT_NODE_TMPL = "{metadata_str}\n\n{content}"
DEFAULT_METADATA_TMPL = "{key}: {value}"
# NOTE: for pretty printing
TRUNCATE_LENGTH = 350
WRAP_WIDTH = 70

ImageType = Union[str, BytesIO]

logger = logging.getLogger(__name__)

EnumNameSerializer = PlainSerializer(
    lambda e: e.value, return_type="str", when_used="always"
)


# 【中文研读】类型职责：所有可序列化组件的基类；提供类型标记、字典/JSON 转换和对象状态兼容，不是存储事务。
class BaseComponent(BaseModel):
    """Base component object to capture class names."""

    # 【中文研读】方法职责：为组件 JSON Schema 增加 class_name 标记
    # 【中文研读】输入参数：core_schema（Pydantic 内部结构定义）；handler（Pydantic 提供的生成或序列化回调）。
    # 【中文研读】返回约定：JsonSchemaValue。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # 【中文研读】处理流程：先调用 Pydantic 生成并展开引用，再在属性字典补入稳定类型名。
        json_schema = handler(core_schema)
        json_schema = handler.resolve_ref_schema(json_schema)

        # inject class name to help with serde
        if "properties" in json_schema:
            json_schema["properties"]["class_name"] = {
                "title": "Class Name",
                "type": "string",
                "default": cls.class_name(),
            }
        return json_schema

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        """
        Get the class name, used as a unique ID in serialization.

        This provides a key that makes serialization robust against actual class
        name changes.
        """
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "base_component"

    # 【中文研读】方法职责：兼容旧 json 调用入口
    # 【中文研读】输入参数：**kwargs（透传选项）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def json(self, **kwargs: Any) -> str:
        # 【中文研读】处理流程：委托 to_json，确保走统一的组件序列化逻辑。
        return self.to_json(**kwargs)

    # 【中文研读】方法职责：在默认序列化结果中加入类型标记
    # 【中文研读】输入参数：handler（Pydantic 提供的生成或序列化回调）；info（Pydantic 校验或序列化上下文）。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @model_serializer(mode="wrap")
    def custom_model_dump(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Dict[str, Any]:
        # 【中文研读】处理流程：先由 handler 导出字段，再补 class_name。
        data = handler(self)
        data["class_name"] = self.class_name()
        return data

    # 【中文研读】方法职责：兼容字典导出入口
    # 【中文研读】输入参数：**kwargs（透传选项）。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    def dict(self, **kwargs: Any) -> Dict[str, Any]:
        # 【中文研读】处理流程：委托 Pydantic model_dump，不访问数据库。
        return self.model_dump(**kwargs)

    # 【中文研读】方法职责：准备可 pickle 的对象状态
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    def __getstate__(self) -> Dict[str, Any]:
        # 【中文研读】处理流程：分别检查普通和私有属性，移除无法 pickle 的项并记警告；被移除的运行资源不会自动恢复。
        state = super().__getstate__()

        # remove attributes that are not pickleable -- kind of dangerous
        # 【中文研读】先收集不可序列化字段名，之后再删除，避免遍历字典时改变字典大小。
        keys_to_remove = []
        for key, val in state["__dict__"].items():
            try:
                # 【中文研读】这里只测试属性是否能被 pickle；失败会从保存状态中移除，不能因此宣称对象的外部连接已可靠持久化。
                pickle.dumps(val)
            except Exception:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            logging.warning(f"Removing unpickleable attribute {key}")
            del state["__dict__"][key]

        # remove private attributes if they aren't pickleable -- kind of dangerous
        # 【中文研读】先收集不可序列化字段名，之后再删除，避免遍历字典时改变字典大小。
        keys_to_remove = []
        private_attrs = state.get("__pydantic_private__", None)
        if private_attrs:
            for key, val in state["__pydantic_private__"].items():
                try:
                    # 【中文研读】这里只测试属性是否能被 pickle；失败会从保存状态中移除，不能因此宣称对象的外部连接已可靠持久化。
                    pickle.dumps(val)
                except Exception:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                logging.warning(f"Removing unpickleable private attribute {key}")
                del state["__pydantic_private__"][key]

        return state

    # 【中文研读】方法职责：恢复对象状态
    # 【中文研读】输入参数：state（序列化的对象状态）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __setstate__(self, state: Dict[str, Any]) -> None:
        # Use the __dict__ and __init__ method to set state
        # so that all variables initialize
        # 【中文研读】处理流程：优先用保存字段重新构造，构造失败时回退父类状态恢复；不等于业务任务恢复。
        try:
            self.__init__(**state["__dict__"])  # type: ignore
        except Exception:
            # Fall back to the default __setstate__ method
            # This may not work if the class had unpickleable attributes
            super().__setstate__(state)

    # 【中文研读】方法职责：导出带 class_name 的字典
    # 【中文研读】输入参数：**kwargs（透传选项）。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    def to_dict(self, **kwargs: Any) -> Dict[str, Any]:
        # 【中文研读】处理流程：调用当前对象 dict 再补类型标记。
        data = self.dict(**kwargs)
        data["class_name"] = self.class_name()
        return data

    # 【中文研读】方法职责：把组件字典编码为 JSON 字符串
    # 【中文研读】输入参数：**kwargs（透传选项）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def to_json(self, **kwargs: Any) -> str:
        # 【中文研读】处理流程：先 to_dict 再 json.dumps。
        data = self.to_dict(**kwargs)
        return json.dumps(data)

    # TODO: return type here not supported by current mypy version
    # 【中文研读】方法职责：从字段字典重建组件
    # 【中文研读】输入参数：data（待恢复的字段字典或数据）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Self。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> Self:  # type: ignore
        # In SimpleKVStore we rely on shallow coping. Hence, the data will be modified in the store directly.
        # And it is the same when the user is passing a dictionary to create a component. We can't modify the passed down dictionary.
        # 【中文研读】处理流程：复制输入避免直接修改调用方对象，用 kwargs 覆盖字段，去掉类型标记后构造。
        data = dict(data)
        if isinstance(kwargs, dict):
            data.update(kwargs)
        # 【中文研读】class_name 用于序列化识别；实际构造时去掉，避免作为多余业务字段传入。
        data.pop("class_name", None)
        return cls(**data)

    # 【中文研读】方法职责：从 JSON 文本恢复组件
    # 【中文研读】输入参数：data_str（JSON 文本）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Self。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_json(cls, data_str: str, **kwargs: Any) -> Self:  # type: ignore
        # 【中文研读】处理流程：解析 JSON，然后复用 from_dict 的字段处理。
        data = json.loads(data_str)
        return cls.from_dict(data, **kwargs)


# 【中文研读】类型职责：节点变换接口：一组节点输入，一组节点输出，供入库处理等阶段组合。
class TransformComponent(BaseComponent, DispatcherSpanMixin):
    """Base class for transform components."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 【中文研读】方法职责：同步节点转换抽象契约；具体子类实现分块、编码等变换，基类没有执行逻辑。
    # 【中文研读】输入参数：nodes（候选节点列表）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Sequence[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> Sequence[BaseNode]:
        """Transform nodes."""

    # 【中文研读】方法职责：默认异步转换直接调用同步 __call__
    # 【中文研读】输入参数：nodes（候选节点列表）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Sequence[BaseNode]。结果的业务含义与失败分支见下面处理流程。
    async def acall(
        self, nodes: Sequence[BaseNode], **kwargs: Any
    ) -> Sequence[BaseNode]:
        """Async transform nodes."""
        # 【中文研读】处理流程：只是接口兼容，耗时同步转换仍可能阻塞事件循环。
        return self.__call__(nodes, **kwargs)


# 【中文研读】类型职责：描述来源、前后片段、父子关系；记录引用关系，不证明来源真实或当前可访问。
class NodeRelationship(str, Enum):
    """
    Node relationships used in `BaseNode` class.

    Attributes:
        SOURCE: The node is the source document.
        PREVIOUS: The node is the previous node in the document.
        NEXT: The node is the next node in the document.
        PARENT: The node is the parent node in the document.
        CHILD: The node is a child node in the document.

    """

    SOURCE = auto()
    PREVIOUS = auto()
    NEXT = auto()
    PARENT = auto()
    CHILD = auto()


# 【中文研读】类型职责：节点对象类别，供序列化、正文补读和内容分派识别类型。
class ObjectType(str, Enum):
    TEXT = auto()
    IMAGE = auto()
    INDEX = auto()
    DOCUMENT = auto()
    MULTIMODAL = auto()


# 【中文研读】类型职责：标记文本、图像、音频或视频模态；当前 P3 文本主线优先看 TextNode。
class Modality(str, Enum):
    TEXT = auto()
    IMAGE = auto()
    AUDIO = auto()
    VIDEO = auto()


# 【中文研读】类型职责：控制拼入文本的元信息范围：ALL 全部、EMBED 按向量排除项、LLM 按模型排除项、NONE 不拼元信息。
class MetadataMode(str, Enum):
    ALL = "all"
    EMBED = "embed"
    LLM = "llm"
    NONE = "none"


# 【中文研读】类型职责：对另一个节点的轻量引用，携带 ID、类型、元数据和可选摘要，不复制完整正文。
class RelatedNodeInfo(BaseComponent):
    node_id: str
    node_type: Annotated[ObjectType, EnumNameSerializer] | str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    hash: Optional[str] = None

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "RelatedNodeInfo"


RelatedNodeType = Union[RelatedNodeInfo, List[RelatedNodeInfo]]


# Node classes for indexes
# 【中文研读】类型职责：节点共同字段与访问接口：ID、向量、metadata、关系和模板；不包含 P3 的可信身份及正式生命周期状态机。
class BaseNode(BaseComponent):
    """
    Base node Object.

    Generic abstract interface for retrievable nodes

    """

    # hash is computed on local field, during the validation process
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    # 【中文研读】节点标识默认随机生成；相同正文不一定同 ID，同 ID 也不自动表达版本。
    id_: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique ID of the node."
    )
    # 【中文研读】可选向量值；创建节点时可以为空，后续编码步骤负责填充。
    embedding: Optional[List[float]] = Field(
        default=None, description="Embedding of the node."
    )

    """"
    metadata fields
    - injected as part of the text shown to LLMs as context
    - injected as part of the text for generating embeddings
    - used by vector DBs for metadata filtering

    """
    # 【中文研读】附加字段，如来源文件名或片段位置；这些字段不能替代经过验证的 P3 Scope。
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="A flat dictionary of metadata fields",
        alias="extra_info",
    )
    # 【中文研读】向量编码文本中排除的 metadata 键，避免无关字段影响相似度。
    excluded_embed_metadata_keys: List[str] = Field(
        default_factory=list,
        description="Metadata keys that are excluded from text for the embed model.",
    )
    # 【中文研读】拼给生成模型的文本中排除的键；这只是文本组织，不是完整敏感字段安全策略。
    excluded_llm_metadata_keys: List[str] = Field(
        default_factory=list,
        description="Metadata keys that are excluded from text for the LLM.",
    )
    # 【中文研读】以关系类型为键保存节点引用；来源、相邻段落、父子片段通过这里连接。
    relationships: Dict[
        Annotated[NodeRelationship, EnumNameSerializer],
        RelatedNodeType,
    ] = Field(
        default_factory=dict,
        description="A mapping of relationships to other node information.",
    )
    metadata_template: str = Field(
        default=DEFAULT_METADATA_TMPL,
        description=(
            "Template for how metadata is formatted, with {key} and "
            "{value} placeholders."
        ),
    )
    metadata_separator: str = Field(
        default="\n",
        description="Separator between metadata fields when converting to string.",
        alias="metadata_seperator",
    )

    # 【中文研读】方法职责：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    @abstractmethod
    def get_type(cls) -> str:
        """Get Object type."""

    # 【中文研读】方法职责：抽象正文读取接口；子类决定如何按 metadata_mode 拼装内容。
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def get_content(self, metadata_mode: MetadataMode = MetadataMode.ALL) -> str:
        """Get object content."""

    # 【中文研读】方法职责：抽象多模态内容块接口；子类负责把节点转换为模型支持的内容结构。
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：list[BaseContentBlock]。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def get_content_blocks(
        self, metadata_mode: MetadataMode = MetadataMode.ALL
    ) -> list[BaseContentBlock]:
        """Get content blocks for the node."""

    # 【中文研读】方法职责：按用途筛选并格式化元信息
    # 【中文研读】输入参数：mode（处理模式，含义由当前类型定义）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def get_metadata_str(self, mode: MetadataMode = MetadataMode.ALL) -> str:
        """Metadata info string."""
        # 【中文研读】处理流程：NONE 返回空，LLM/EMBED 删除各自排除键，保留项按模板拼接。
        if mode == MetadataMode.NONE:
            return ""

        # 【中文研读】先允许所有元信息，再按用途减去排除键；这只是格式化时的选择，不修改原 metadata。
        usable_metadata_keys = set(self.metadata.keys())
        # 【中文研读】面向回答模型时使用 LLM 排除规则；Embedding 用另一个排除列表，两者可以不同。
        if mode == MetadataMode.LLM:
            for key in self.excluded_llm_metadata_keys:
                if key in usable_metadata_keys:
                    usable_metadata_keys.remove(key)
        # 【中文研读】生成向量时移除不应参与相似度计算的元信息键。
        elif mode == MetadataMode.EMBED:
            for key in self.excluded_embed_metadata_keys:
                if key in usable_metadata_keys:
                    usable_metadata_keys.remove(key)

        return self.metadata_separator.join(
            [
                self.metadata_template.format(key=key, value=str(value))
                for key, value in self.metadata.items()
                if key in usable_metadata_keys
            ]
        )

    # 【中文研读】方法职责：把可见元信息包装成文本块
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：list[BaseContentBlock]。结果的业务含义与失败分支见下面处理流程。
    def get_metadata_content_blocks(
        self, metadata_mode: MetadataMode
    ) -> list[BaseContentBlock]:
        """Get metadata content block if metadata_mode is not NONE."""
        # 【中文研读】处理流程：NONE 或空元信息返回空列表，其余生成一个 TextBlock。
        from llama_index.core.base.llms.types import TextBlock

        if metadata_mode == MetadataMode.NONE:
            return []
        metadata_str = self.get_metadata_str(mode=metadata_mode).strip()
        if not metadata_str:
            return []
        return [TextBlock(text=metadata_str)]

    # 【中文研读】方法职责：抽象正文修改接口；子类定义本地字段更新方式，不代表持久存储已更新。
    # 【中文研读】输入参数：value（待赋值或转换的数据）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @abstractmethod
    def set_content(self, value: Any) -> None:
        """Set the content of the node."""

    # 【中文研读】方法职责：抽象内容摘要接口；具体算法由节点类型决定，不是 P3 的 memory_id/version。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    @abstractmethod
    def hash(self) -> str:
        """Get hash of node."""

    # 【中文研读】方法职责：读取并返回 self.id_；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def node_id(self) -> str:
        # 【中文研读】处理流程：读取并返回 self.id_；此入口不发起检索或存储写入。
        return self.id_

    # 【中文研读】方法职责：将传入值 value 写入 self.id_；修改的是当前对象属性。
    # 【中文研读】输入参数：value（待赋值或转换的数据）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @node_id.setter
    def node_id(self, value: str) -> None:
        # 【中文研读】处理流程：将传入值 value 写入 self.id_；修改的是当前对象属性。
        self.id_ = value

    # 【中文研读】方法职责：读取来源关系
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[RelatedNodeInfo]。结果的业务含义与失败分支见下面处理流程。
    @property
    def source_node(self) -> Optional[RelatedNodeInfo]:
        """
        Source object node.

        Extracted from the relationships field.

        """
        # 【中文研读】处理流程：不存在返回 None，来源应是单一引用，列表形态抛错。
        if NodeRelationship.SOURCE not in self.relationships:
            return None

        relation = self.relationships[NodeRelationship.SOURCE]
        if isinstance(relation, list):
            raise ValueError("Source object must be a single RelatedNodeInfo object")
        return relation

    # 【中文研读】方法职责：读取前一片段引用
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[RelatedNodeInfo]。结果的业务含义与失败分支见下面处理流程。
    @property
    def prev_node(self) -> Optional[RelatedNodeInfo]:
        """Prev node."""
        # 【中文研读】处理流程：无关系返回 None，存在时要求 RelatedNodeInfo，错误类型显式失败。
        if NodeRelationship.PREVIOUS not in self.relationships:
            return None

        relation = self.relationships[NodeRelationship.PREVIOUS]
        if not isinstance(relation, RelatedNodeInfo):
            raise ValueError("Previous object must be a single RelatedNodeInfo object")
        return relation

    # 【中文研读】方法职责：读取后一片段引用
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[RelatedNodeInfo]。结果的业务含义与失败分支见下面处理流程。
    @property
    def next_node(self) -> Optional[RelatedNodeInfo]:
        """Next node."""
        # 【中文研读】处理流程：先检查关系存在，再校验是单个节点引用。
        if NodeRelationship.NEXT not in self.relationships:
            return None

        relation = self.relationships[NodeRelationship.NEXT]
        if not isinstance(relation, RelatedNodeInfo):
            raise ValueError("Next object must be a single RelatedNodeInfo object")
        return relation

    # 【中文研读】方法职责：读取父节点引用
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[RelatedNodeInfo]。结果的业务含义与失败分支见下面处理流程。
    @property
    def parent_node(self) -> Optional[RelatedNodeInfo]:
        """Parent node."""
        # 【中文研读】处理流程：无父关系返回 None，错误关系形态抛错。
        if NodeRelationship.PARENT not in self.relationships:
            return None

        relation = self.relationships[NodeRelationship.PARENT]
        if not isinstance(relation, RelatedNodeInfo):
            raise ValueError("Parent object must be a single RelatedNodeInfo object")
        return relation

    # 【中文研读】方法职责：读取子节点引用列表
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[List[RelatedNodeInfo]]。结果的业务含义与失败分支见下面处理流程。
    @property
    def child_nodes(self) -> Optional[List[RelatedNodeInfo]]:
        """Child nodes."""
        # 【中文研读】处理流程：无子关系返回 None，单个引用代替列表时抛错。
        if NodeRelationship.CHILD not in self.relationships:
            return None

        relation = self.relationships[NodeRelationship.CHILD]
        if not isinstance(relation, list):
            raise ValueError("Child objects must be a list of RelatedNodeInfo objects.")
        return relation

    # 【中文研读】方法职责：从来源关系得到原文档 ID
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[str]。结果的业务含义与失败分支见下面处理流程。
    @property
    def ref_doc_id(self) -> Optional[str]:  # pragma: no cover
        """Deprecated: Get ref doc id."""
        # 【中文研读】处理流程：没有来源返回 None，否则取引用 node_id，不现场读取文档。
        source_node = self.source_node
        if source_node is None:
            return None
        return source_node.node_id

    # 【中文研读】方法职责：读取并返回 self.metadata；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @property
    @deprecated(
        version="0.12.2",
        reason="'extra_info' is deprecated, use 'metadata' instead.",
    )
    def extra_info(self) -> dict[str, Any]:  # pragma: no coverde
        # 【中文研读】处理流程：读取并返回 self.metadata；此入口不发起检索或存储写入。
        return self.metadata

    # 【中文研读】方法职责：将传入值 extra_info 写入 self.metadata；修改的是当前对象属性。
    # 【中文研读】输入参数：extra_info（旧命名的元信息字典）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @extra_info.setter
    @deprecated(
        version="0.12.2",
        reason="'extra_info' is deprecated, use 'metadata' instead.",
    )
    def extra_info(self, extra_info: dict[str, Any]) -> None:  # pragma: no coverde
        # 【中文研读】处理流程：将传入值 extra_info 写入 self.metadata；修改的是当前对象属性。
        self.metadata = extra_info

    # 【中文研读】方法职责：生成便于调试阅读的节点摘要
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def __str__(self) -> str:
        # 【中文研读】处理流程：截断正文、按宽度换行并附节点 ID；不是最终 ContextPack。
        source_text_truncated = truncate_text(
            self.get_content().strip(), TRUNCATE_LENGTH
        )
        source_text_wrapped = textwrap.fill(
            f"Text: {source_text_truncated}\n", width=WRAP_WIDTH
        )
        return f"Node ID: {self.node_id}\n{source_text_wrapped}"

    # 【中文研读】方法职责：取得节点中已保存的向量
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[float]。结果的业务含义与失败分支见下面处理流程。
    def get_embedding(self) -> List[float]:
        """
        Get embedding.

        Errors if embedding is None.

        """
        # 【中文研读】处理流程：缺失时抛错，不在此方法自动运行 Embedding 模型。
        # 【中文研读】没有向量时明确报错；此 getter 不会自行加载 BGE 或调用远端模型。
        if self.embedding is None:
            raise ValueError("embedding not set.")
        return self.embedding

    # 【中文研读】方法职责：把完整节点缩减为关系引用
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：RelatedNodeInfo。结果的业务含义与失败分支见下面处理流程。
    def as_related_node_info(self) -> RelatedNodeInfo:
        """Get node as RelatedNodeInfo."""
        # 【中文研读】处理流程：保留 ID、类型、metadata 和 hash，供其他节点关联。
        return RelatedNodeInfo(
            node_id=self.node_id,
            node_type=self.get_type(),
            metadata=self.metadata,
            hash=self.hash,
        )


EmbeddingKind = Literal["sparse", "dense"]


# 【中文研读】类型职责：单种媒体的内容或定位信息，支持文本、字节、路径、URL 与向量；某些读取方法会访问文件或网络。
class MediaResource(BaseModel):
    """
    A container class for media content.

    This class represents a generic media resource that can be stored and accessed
    in multiple ways - as raw bytes, on the filesystem, or via URL. It also supports
    storing vector embeddings for the media content.

    Attributes:
        embeddings: Multi-vector dict representation of this resource for embedding-based search/retrieval
        text: Plain text representation of this resource
        data: Raw binary data of the media content
        mimetype: The MIME type indicating the format/type of the media content
        path: Local filesystem path where the media content can be accessed
        url: URL where the media content can be accessed remotely

    """

    embeddings: dict[EmbeddingKind, list[float]] | None = Field(
        default=None, description="Vector representation of this resource."
    )
    data: bytes | None = Field(
        default=None,
        exclude=True,
        description="base64 binary representation of this resource.",
    )
    text: str | None = Field(
        default=None, description="Text representation of this resource."
    )
    path: Path | None = Field(
        default=None, description="Filesystem path of this resource."
    )
    url: AnyUrl | None = Field(default=None, description="URL to reach this resource.")
    mimetype: str | None = Field(
        default=None, description="MIME type of this resource."
    )

    model_config = {
        # This ensures validation runs even for None values
        "validate_default": True
    }

    # 【中文研读】方法职责：将媒体字节规范成 base64 表示
    # 【中文研读】输入参数：v（待校验的字段值）；info（Pydantic 校验或序列化上下文）。
    # 【中文研读】返回约定：bytes | None。结果的业务含义与失败分支见下面处理流程。
    @field_validator("data", mode="after")
    @classmethod
    def validate_data(cls, v: bytes | None, info: ValidationInfo) -> bytes | None:
        """
        If binary data was passed, store the resource as base64 and guess the mimetype when possible.

        In case the model was built passing binary data but without a mimetype,
        we try to guess it using the filetype library. To avoid resource-intense
        operations, we won't load the path or the URL to guess the mimetype.
        """
        # 【中文研读】处理流程：None 保留，能按 base64 解码则原样保留，否则对原字节编码；不是图片真实性校验。
        # 【中文研读】缺省媒体字节允许保持空，后续可以使用路径或 URL 定位。
        if v is None:
            return v

        try:
            # Check if data is already base64 encoded.
            # b64decode() can succeed on random binary data, so we
            # pass verify=True to make sure it's not a false positive
            decoded = base64.b64decode(v, validate=True)
        except BinasciiError:
            # b64decode failed, return encoded
            return base64.b64encode(v)

        # Good as is, return unchanged
        return v

    # 【中文研读】方法职责：尽量推断媒体 MIME 类型
    # 【中文研读】输入参数：v（待校验的字段值）；info（Pydantic 校验或序列化上下文）。
    # 【中文研读】返回约定：str | None。结果的业务含义与失败分支见下面处理流程。
    @field_validator("mimetype", mode="after")
    @classmethod
    def validate_mimetype(cls, v: str | None, info: ValidationInfo) -> str | None:
        # 【中文研读】处理流程：显式值优先，其次检查内嵌数据，再根据路径扩展名猜测，无法判断保持原值。
        if v is not None:
            return v

        # Since this field validator runs after the one for `data`
        # then the contents of `data` should be encoded already
        b64_data = info.data.get("data")
        if b64_data:  # encoded bytes
            decoded_data = base64.b64decode(b64_data)
            if guess := filetype.guess(decoded_data):
                return guess.mime

        # guess from path
        rpath: str | None = info.data["path"]
        if rpath:
            extension = Path(rpath).suffix.replace(".", "")
            if ftype := filetype.get_type(ext=extension):
                return ftype.mime

        return v

    # 【中文研读】方法职责：将 Path 转成字符串方便 JSON 表示
    # 【中文研读】输入参数：path（文件系统路径）；_info（本实现未使用的校验上下文）。
    # 【中文研读】返回约定：Optional[str]。结果的业务含义与失败分支见下面处理流程。
    @field_serializer("path")  # type: ignore
    def serialize_path(
        self, path: Optional[Path], _info: ValidationInfo
    ) -> Optional[str]:
        # 【中文研读】处理流程：None 保留，其他值转 str，不读取文件。
        if path is None:
            return path
        return str(path)

    # 【中文研读】方法职责：按资源字段构造摘要
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def hash(self) -> str:
        """
        Generate a hash to uniquely identify the media resource.

        The hash is generated based on the available content (data, path, text or url).
        Returns an empty string if no content is available (all fields are None).
        Note: An empty string for text (text="") is treated as valid content and
        will produce a different hash than text=None.
        """
        # 【中文研读】处理流程：收集文本、数据摘要、路径字符串摘要和 URL 字符串摘要，再汇总 SHA256；路径和 URL 并未下载内容计算。
        bits: list[str] = []
        if self.text is not None:
            # Use marker for empty string to distinguish from None
            if self.text == "":
                bits.append("<empty_string>")
            else:
                bits.append(self.text)
        if self.data is not None:
            # Hash the binary data if available
            bits.append(str(sha256(self.data).hexdigest()))
        if self.path is not None:
            # Hash the file path if provided
            bits.append(str(sha256(str(self.path).encode("utf-8")).hexdigest()))
        if self.url is not None:
            # Use the URL string as basis for hash
            bits.append(str(sha256(str(self.url).encode("utf-8")).hexdigest()))

        if not bits:
            return ""
        doc_identity = "".join(bits)
        return str(sha256(doc_identity.encode("utf-8")).hexdigest())


# 【中文研读】类型职责：多模态节点，将文本/图像/音频/视频资源放在统一对象里，并可转换为模型内容块。
class Node(BaseNode):
    text_resource: MediaResource | None = Field(
        default=None, description="Text content of the node."
    )
    image_resource: MediaResource | None = Field(
        default=None, description="Image content of the node."
    )
    audio_resource: MediaResource | None = Field(
        default=None, description="Audio content of the node."
    )
    video_resource: MediaResource | None = Field(
        default=None, description="Video content of the node."
    )
    text_template: str = Field(
        default=DEFAULT_TEXT_NODE_TMPL,
        description=(
            "Template for how text_resource is formatted, with {content} and "
            "{metadata_str} placeholders."
        ),
    )

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "Node"

    # 【中文研读】方法职责：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_type(cls) -> str:
        """Get Object type."""
        # 【中文研读】处理流程：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
        return ObjectType.MULTIMODAL

    # 【中文研读】方法职责：读取多模态节点中的文本资源
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def get_content(self, metadata_mode: MetadataMode = MetadataMode.NONE) -> str:
        """
        Get the text content for the node if available.

        Provided for backward compatibility, use self.text_resource directly instead.
        """
        # 【中文研读】处理流程：无文本返回空，按用途取得 metadata，需要时套文本模板，否则直接返回文本。
        if self.text_resource:
            metadata_str = self.get_metadata_str(metadata_mode)
            # 【中文研读】无需拼元信息时直接返回正文，避免引入空标题或额外模板字符。
            if metadata_mode == MetadataMode.NONE or not metadata_str:
                return self.text_resource.text or ""

            return self.text_template.format(
                content=self.text_resource.text or "",
                metadata_str=metadata_str,
            ).strip()
        return ""

    # 【中文研读】方法职责：按模态生成模型内容块
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：list[BaseContentBlock]。结果的业务含义与失败分支见下面处理流程。
    def get_content_blocks(
        self, metadata_mode: MetadataMode = MetadataMode.NONE
    ) -> list[BaseContentBlock]:
        """
        Get content blocks for the node.
        """
        # 【中文研读】处理流程：先加入元信息块，再分别添加文本、图像、音频和视频块，保持各自数据定位信息。
        from llama_index.core.base.llms.types import (
            TextBlock,
            ImageBlock,
            AudioBlock,
            VideoBlock,
        )

        blocks: list[BaseContentBlock] = []
        blocks.extend(self.get_metadata_content_blocks(metadata_mode))
        if self.text_resource:
            blocks.append(TextBlock(text=self.text_resource.text or ""))
        if self.image_resource:
            blocks.append(
                ImageBlock(
                    image=self.image_resource.data,
                    url=self.image_resource.url,
                    path=self.image_resource.path,
                    image_mimetype=self.image_resource.mimetype,
                )
            )
        if self.audio_resource:
            guess = filetype.get_type(mime=self.audio_resource.mimetype)
            blocks.append(
                AudioBlock(
                    audio=self.audio_resource.data,
                    url=self.audio_resource.url,
                    path=self.audio_resource.path,
                    format=guess.extension if guess else None,
                )
            )
        if self.video_resource:
            blocks.append(
                VideoBlock(
                    video=self.video_resource.data,
                    url=self.video_resource.url,
                    path=self.video_resource.path,
                    video_mimetype=self.video_resource.mimetype,
                )
            )

        return blocks

    # 【中文研读】方法职责：把字符串包装成文本 MediaResource
    # 【中文研读】输入参数：value（待赋值或转换的数据）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def set_content(self, value: str) -> None:
        """
        Set the text content of the node.

        Provided for backward compatibility, set self.text_resource instead.
        """
        # 【中文研读】处理流程：替换当前对象的 text_resource，其他模态保持原状。
        self.text_resource = MediaResource(text=value)

    # 【中文研读】方法职责：将全部元信息与存在的媒体资源摘要合并
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def hash(self) -> str:
        """
        Generate a hash representing the state of the node.

        The hash is generated based on the available resources (audio, image, text or video) and its metadata.
        """
        # 【中文研读】处理流程：依次收集各资源 hash，连接后再 SHA256；metadata 改变可能导致 hash 改变。
        doc_identities = []
        metadata_str = self.get_metadata_str(mode=MetadataMode.ALL)
        if metadata_str:
            doc_identities.append(metadata_str)
        if self.audio_resource is not None:
            doc_identities.append(self.audio_resource.hash)
        if self.image_resource is not None:
            doc_identities.append(self.image_resource.hash)
        if self.text_resource is not None:
            doc_identities.append(self.text_resource.hash)
        if self.video_resource is not None:
            doc_identities.append(self.video_resource.hash)

        doc_identity = "-".join(doc_identities)
        return str(sha256(doc_identity.encode("utf-8", "surrogatepass")).hexdigest())


# 【中文研读】类型职责：文本节点，保存正文、字符位置、文本格式模板；检索主线首先理解它。
class TextNode(BaseNode):
    """Provided for backward compatibility."""

    # 【中文研读】方法职责：兼容新版 text_resource 输入
    # 【中文研读】输入参数：*args（透传选项）；**kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Make TextNode forward-compatible with Node by supporting 'text_resource' in the constructor."""
        # 【中文研读】处理流程：从资源对象或字典取 text，改写为旧 TextNode 字段，再交给父类校验。
        if "text_resource" in kwargs:
            tr = kwargs.pop("text_resource")
            if isinstance(tr, MediaResource):
                kwargs["text"] = tr.text
            else:
                kwargs["text"] = tr["text"]
        super().__init__(*args, **kwargs)

    # 【中文研读】正文文本；与 metadata、embedding 分开保存，避免把不同含义混成一个字符串。
    text: str = Field(default="", description="Text content of the node.")
    mimetype: str = Field(
        default="text/plain", description="MIME type of the node content."
    )
    # 【中文研读】片段在来源中的起始字符位置；P3 还需绑定精确来源版本。
    start_char_idx: Optional[int] = Field(
        default=None, description="Start char index of the node."
    )
    # 【中文研读】片段在来源中的结束字符位置；不是 token 数或向量维度。
    end_char_idx: Optional[int] = Field(
        default=None, description="End char index of the node."
    )
    text_template: str = Field(
        default=DEFAULT_TEXT_NODE_TMPL,
        description=(
            "Template for how text is formatted, with {content} and "
            "{metadata_str} placeholders."
        ),
    )

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "TextNode"

    # 【中文研读】方法职责：对文本与 metadata 的字符串表示求 SHA256
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def hash(self) -> str:
        # 【中文研读】处理流程：相同正文但 metadata 不同可得到不同 hash，不能直接代替业务版本身份。
        # 【中文研读】TextNode 的 hash 同时依赖文本和元信息；按 hash 去重可能受 metadata 变化影响。
        doc_identity = str(self.text) + str(self.metadata)
        return str(sha256(doc_identity.encode("utf-8", "surrogatepass")).hexdigest())

    # 【中文研读】方法职责：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_type(cls) -> str:
        """Get Object type."""
        # 【中文研读】处理流程：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
        return ObjectType.TEXT

    # 【中文研读】方法职责：按指定用途拼正文与元信息
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def get_content(self, metadata_mode: MetadataMode = MetadataMode.NONE) -> str:
        """Get object content."""
        # 【中文研读】处理流程：元信息为空或 NONE 时直接返回正文，否则用 text_template 格式化。
        metadata_str = self.get_metadata_str(mode=metadata_mode).strip()
        # 【中文研读】无需拼元信息时直接返回正文，避免引入空标题或额外模板字符。
        if metadata_mode == MetadataMode.NONE or not metadata_str:
            return self.text

        return self.text_template.format(
            content=self.text, metadata_str=metadata_str
        ).strip()

    # 【中文研读】方法职责：生成文本节点内容块
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：list[BaseContentBlock]。结果的业务含义与失败分支见下面处理流程。
    def get_content_blocks(
        self, metadata_mode: MetadataMode = MetadataMode.NONE
    ) -> list[BaseContentBlock]:
        """Get content blocks for the node."""
        # 【中文研读】处理流程：按用途加入元信息块，再追加正文 TextBlock。
        from llama_index.core.base.llms.types import TextBlock

        blocks: list[BaseContentBlock] = []
        blocks.extend(self.get_metadata_content_blocks(metadata_mode))
        blocks.append(TextBlock(text=self.text))
        return blocks

    # 【中文研读】方法职责：将传入值 value 写入 self.text；修改的是当前对象属性。
    # 【中文研读】输入参数：value（待赋值或转换的数据）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def set_content(self, value: str) -> None:
        """Set the content of the node."""
        # 【中文研读】处理流程：将传入值 value 写入 self.text；修改的是当前对象属性。
        self.text = value

    # 【中文研读】方法职责：返回正文在来源中的起止字符位置
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    def get_node_info(self) -> Dict[str, Any]:
        """Get node info."""
        # 【中文研读】处理流程：组装 start/end 字典，不负责验证位置对应的来源版本。
        return {"start": self.start_char_idx, "end": self.end_char_idx}

    # 【中文研读】方法职责：只取正文不拼 metadata
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def get_text(self) -> str:
        # 【中文研读】处理流程：调用 get_content 并明确使用 NONE。
        return self.get_content(metadata_mode=MetadataMode.NONE)

    # 【中文研读】方法职责：保留旧属性入口
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @property
    @deprecated(
        version="0.12.2",
        reason="'node_info' is deprecated, use 'get_node_info' instead.",
    )
    def node_info(self) -> Dict[str, Any]:
        """Deprecated: Get node info."""
        # 【中文研读】处理流程：委托 get_node_info，避免重复维护字符位置逻辑。
        return self.get_node_info()


# 【中文研读】类型职责：历史图像节点兼容结构；支持内嵌数据、文件和 URL，读取媒体会产生 IO。
class ImageNode(TextNode):
    """Node with image."""

    # TODO: store reference instead of actual image
    # base64 encoded image str
    image: Optional[str] = None
    image_path: Optional[str] = None
    image_url: Optional[str] = None
    image_mimetype: Optional[str] = None
    text_embedding: Optional[List[float]] = Field(
        default=None,
        description="Text embedding of image node, if text field is filled out",
    )

    # 【中文研读】方法职责：把 image_resource 映射为旧图像字段
    # 【中文研读】输入参数：*args（透传选项）；**kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Make ImageNode forward-compatible with Node by supporting 'image_resource' in the constructor."""
        # 【中文研读】处理流程：区分对象与字典，必要时用路径扩展名补 MIME，最后父类构造。
        if "image_resource" in kwargs:
            ir = kwargs.pop("image_resource")
            if isinstance(ir, MediaResource):
                kwargs["image_path"] = ir.path.as_posix() if ir.path else None
                kwargs["image_url"] = ir.url
                kwargs["image_mimetype"] = ir.mimetype
            else:
                kwargs["image_path"] = ir.get("path", None)
                kwargs["image_url"] = ir.get("url", None)
                kwargs["image_mimetype"] = ir.get("mimetype", None)

        mimetype = kwargs.get("image_mimetype")
        if not mimetype and kwargs.get("image_path") is not None:
            # guess mimetype from image_path
            extension = Path(kwargs["image_path"]).suffix.replace(".", "")
            if ftype := filetype.get_type(ext=extension):
                kwargs["image_mimetype"] = ftype.mime

        super().__init__(*args, **kwargs)

    # 【中文研读】方法职责：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_type(cls) -> str:
        # 【中文研读】处理流程：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
        return ObjectType.IMAGE

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "ImageNode"

    # 【中文研读】方法职责：将图像定位转换为可读数据
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：ImageType。结果的业务含义与失败分支见下面处理流程。
    def resolve_image(self) -> ImageType:
        """Resolve an image such that PIL can read it."""
        # 【中文研读】处理流程：内嵌 base64 优先，其次文件路径，最后联网取 URL；无任何来源抛错。
        if self.image is not None:
            import base64

            return BytesIO(base64.b64decode(self.image))
        elif self.image_path is not None:
            return self.image_path
        elif self.image_url is not None:
            # load image from URL
            import requests

            response = requests.get(self.image_url, timeout=(60, 60))
            return BytesIO(response.content)
        else:
            raise ValueError("No image found in node.")

    # 【中文研读】方法职责：组合图像数据、路径、URL 与文本求摘要
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def hash(self) -> str:
        """Get hash of node."""
        # doc identity depends on if image, image_path, or image_url is set
        # 【中文研读】处理流程：缺失字段用占位字符串，再 SHA256；不下载 URL 内容验证新旧。
        image_str = self.image or "None"
        image_path_str = self.image_path or "None"
        image_url_str = self.image_url or "None"
        image_text = self.text or "None"
        doc_identity = f"{image_str}-{image_path_str}-{image_url_str}-{image_text}"
        return str(sha256(doc_identity.encode("utf-8", "surrogatepass")).hexdigest())

    # 【中文研读】方法职责：生成图像内容块
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：list[BaseContentBlock]。结果的业务含义与失败分支见下面处理流程。
    def get_content_blocks(
        self, metadata_mode: MetadataMode = MetadataMode.NONE
    ) -> list[BaseContentBlock]:
        """Get content blocks for the node."""
        # 【中文研读】处理流程：先元信息，解析图像，字节流读出内容，路径型则保留定位字段。
        from llama_index.core.base.llms.types import ImageBlock

        blocks: list[BaseContentBlock] = []
        blocks.extend(self.get_metadata_content_blocks(metadata_mode))
        resolved = self.resolve_image()
        if isinstance(resolved, BytesIO):
            image_data: bytes | None = resolved.read()
        else:
            image_data = None
        blocks.append(
            ImageBlock(
                image=image_data,
                url=self.image_url,
                path=self.image_path,
                image_mimetype=self.image_mimetype,
            )
        )
        return blocks


# 【中文研读】类型职责：文本节点附带一个可引用对象，例如另一个检索器或查询引擎，供 BaseRetriever 递归展开。
class IndexNode(TextNode):
    """
    Node with reference to any object.

    This can include other indices, query engines, retrievers.

    This can also include other nodes (though this is overlapping with `relationships`
    on the Node class).

    """

    # 【中文研读】被引用的索引或检索对象标识，供 BaseRetriever.object_map 定位。
    index_id: str
    # 【中文研读】可直接附带运行对象；可能不能序列化，因此需要专用转换逻辑。
    obj: Any = None

    # 【中文研读】方法职责：序列化 IndexNode 引用对象
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Any。结果的业务含义与失败分支见下面处理流程。
    def _serialize_obj(self) -> Any:
        # 【中文研读】处理流程：空对象保留，节点使用节点专用序列化，Pydantic 导出字典，其他对象尝试 JSON；失败抛错。
        from llama_index.core.storage.docstore.utils import doc_to_json

        try:
            if self.obj is None:
                return None
            elif isinstance(self.obj, BaseNode):
                return doc_to_json(self.obj)
            elif isinstance(self.obj, BaseModel):
                return self.obj.model_dump()
            else:
                return json.dumps(self.obj)
        except Exception:
            raise ValueError("IndexNode obj is not serializable: " + str(self.obj))

    # 【中文研读】方法职责：在父类序列化结果中补入引用对象
    # 【中文研读】输入参数：handler（Pydantic 提供的生成或序列化回调）；info（Pydantic 校验或序列化上下文）。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @model_serializer(mode="wrap")
    def custom_model_dump(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Dict[str, Any]:
        # 【中文研读】处理流程：通过 _serialize_obj 处理 obj，避免直接序列化运行实例失败。
        data = super().custom_model_dump(handler, info)
        data["obj"] = self._serialize_obj()
        return data

    # 【中文研读】方法职责：兼容字典导出并补 obj
    # 【中文研读】输入参数：**kwargs（透传选项）。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    def dict(self, **kwargs: Any) -> Dict[str, Any]:
        # 【中文研读】处理流程：先父类导出字段，再序列化引用对象。
        data = super().dict(**kwargs)
        data["obj"] = self._serialize_obj()
        return data

    # 【中文研读】方法职责：由 TextNode 创建 IndexNode
    # 【中文研读】输入参数：node（单个节点）；index_id（被引用索引对象的标识）。
    # 【中文研读】返回约定：IndexNode。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_text_node(
        cls,
        node: TextNode,
        index_id: str,
    ) -> IndexNode:
        """Create index node from text node."""
        # copy all attributes from text node, add index id
        # 【中文研读】处理流程：复制文本节点字段，附加 index_id，形成可递归引用的节点。
        return cls(
            **node.dict(),
            index_id=index_id,
        )

    # TODO: return type here not supported by current mypy version
    # 【中文研读】方法职责：恢复索引节点及引用对象
    # 【中文研读】输入参数：data（待恢复的字段字典或数据）；**kwargs（透传选项）。
    # 【中文研读】返回约定：Self。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> Self:  # type: ignore
        # 【中文研读】处理流程：先恢复节点字段，字符串引用包装为 TextNode，字典优先按节点解析，失败退为字符串节点。
        output = super().from_dict(data, **kwargs)

        obj = data.get("obj")
        parsed_obj = None

        if isinstance(obj, str):
            parsed_obj = TextNode(text=obj)
        elif isinstance(obj, dict):
            from llama_index.core.storage.docstore.utils import json_to_doc

            # check if its a node, else assume stringable
            try:
                parsed_obj = json_to_doc(obj)  # type: ignore[assignment]
            except Exception:
                parsed_obj = TextNode(text=str(obj))

        output.obj = parsed_obj

        return output

    # 【中文研读】方法职责：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_type(cls) -> str:
        # 【中文研读】处理流程：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
        return ObjectType.INDEX

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "IndexNode"


# 【中文研读】类型职责：包装一个节点和可选分数；分数可能是向量相似度、融合分数或重排分数，类型本身不定义统一概率含义。
class NodeWithScore(BaseComponent):
    # 【中文研读】实际候选节点，由此取得正文、ID、metadata 与向量。
    node: SerializeAsAny[BaseNode]
    # 【中文研读】可选评分，含义由产生它的检索器/融合器/重排器决定。
    score: Optional[float] = None

    # 【中文研读】方法职责：生成节点与分数的调试文本
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def __str__(self) -> str:
        # 【中文研读】处理流程：数值格式化，未设分数显示 None，不改变排序。
        score_str = "None" if self.score is None else f"{self.score: 0.3f}"
        return f"{self.node}\nScore: {score_str}\n"

    # 【中文研读】方法职责：取得候选分数
    # 【中文研读】输入参数：raise_error（分数缺失时是否抛错）。
    # 【中文研读】返回约定：float。结果的业务含义与失败分支见下面处理流程。
    def get_score(self, raise_error: bool = False) -> float:
        """Get score."""
        # 【中文研读】处理流程：缺失时按 raise_error 决定抛错或返回 0，有值原样返回；0 不是模型重新评分的结果。
        # 【中文研读】分数未设与低分是不同情况；调用方选择抛错或以零值参与后续处理。
        if self.score is None:
            if raise_error:
                raise ValueError("Score not set.")
            else:
                return 0.0
        else:
            return self.score

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "NodeWithScore"

    ##### pass through methods to BaseNode #####
    # 【中文研读】方法职责：读取并返回 self.node.node_id；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def node_id(self) -> str:
        # 【中文研读】处理流程：读取并返回 self.node.node_id；此入口不发起检索或存储写入。
        return self.node.node_id

    # 【中文研读】方法职责：读取并返回 self.node.id_；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def id_(self) -> str:
        # 【中文研读】处理流程：读取并返回 self.node.id_；此入口不发起检索或存储写入。
        return self.node.id_

    # 【中文研读】方法职责：便利读取底层 TextNode 正文
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def text(self) -> str:
        # 【中文研读】处理流程：类型不匹配时抛错，不能对所有模态假定都有 text 属性。
        # 【中文研读】便利属性只适用于文本节点；跨模态调用不能默认把所有节点都当字符串。
        if isinstance(self.node, TextNode):
            return self.node.text
        else:
            raise ValueError("Node must be a TextNode to get text.")

    # 【中文研读】方法职责：读取并返回 self.node.metadata；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @property
    def metadata(self) -> Dict[str, Any]:
        # 【中文研读】处理流程：读取并返回 self.node.metadata；此入口不发起检索或存储写入。
        return self.node.metadata

    # 【中文研读】方法职责：读取并返回 self.node.embedding；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Optional[List[float]]。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding(self) -> Optional[List[float]]:
        # 【中文研读】处理流程：读取并返回 self.node.embedding；此入口不发起检索或存储写入。
        return self.node.embedding

    # 【中文研读】方法职责：委托底层文本节点读取正文
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def get_text(self) -> str:
        # 【中文研读】处理流程：先要求 TextNode 类型，其他节点明确失败。
        # 【中文研读】便利属性只适用于文本节点；跨模态调用不能默认把所有节点都当字符串。
        if isinstance(self.node, TextNode):
            return self.node.get_text()
        else:
            raise ValueError("Node must be a TextNode to get text.")

    # 【中文研读】方法职责：把读取请求透传给底层节点
    # 【中文研读】输入参数：metadata_mode（哪些元信息应拼入内容）。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def get_content(self, metadata_mode: MetadataMode = MetadataMode.NONE) -> str:
        # 【中文研读】处理流程：保留 metadata_mode 以控制哪些元信息进入文本。
        return self.node.get_content(metadata_mode=metadata_mode)

    # 【中文研读】方法职责：委托节点取得已有向量
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[float]。结果的业务含义与失败分支见下面处理流程。
    def get_embedding(self) -> List[float]:
        # 【中文研读】处理流程：由节点决定缺失时的异常，不在包装层执行模型。
        return self.node.get_embedding()


# Document Classes for Readers


# 【中文研读】类型职责：文档级节点及兼容转换入口；保留来源文档内容，可转换到其他框架的数据结构。
class Document(Node):
    """
    Generic interface for a data document.

    This document connects to data sources.
    """

    # 【中文研读】方法职责：兼容旧文档字段名称
    # 【中文研读】输入参数：**data（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(self, **data: Any) -> None:
        """
        Keeps backward compatibility with old 'Document' versions.

        If 'text' was passed, store it in 'text_resource'.
        If 'doc_id' was passed, store it in 'id_'.
        If 'extra_info' was passed, store it in 'metadata'.
        """
        # 【中文研读】处理流程：doc_id→id_，extra_info→metadata，text→text_resource；新旧同时存在时保留新字段并记录警告。
        # 【中文研读】兼容旧 API：旧名字转换到当前字段；已有新字段时优先保留新字段。
        if "doc_id" in data:
            value = data.pop("doc_id")
            if "id_" in data:
                msg = "'doc_id' is deprecated and 'id_' will be used instead"
                logging.warning(msg)
            else:
                data["id_"] = value

        if "extra_info" in data:
            value = data.pop("extra_info")
            if "metadata" in data:
                msg = "'extra_info' is deprecated and 'metadata' will be used instead"
                logging.warning(msg)
            else:
                data["metadata"] = value

        if data.get("text"):
            text = data.pop("text")
            if "text_resource" in data:
                text_resource = (
                    data["text_resource"]
                    if isinstance(data["text_resource"], MediaResource)
                    else MediaResource.model_validate(data["text_resource"])
                )
                if (text_resource.text or "").strip() != text.strip():
                    msg = (
                        "'text' is deprecated and 'text_resource' will be used instead"
                    )
                    logging.warning(msg)
            else:
                data["text_resource"] = MediaResource(text=text)

        super().__init__(**data)

    # 【中文研读】方法职责：导出时兼容旧 text 字段
    # 【中文研读】输入参数：handler（Pydantic 提供的生成或序列化回调）；info（Pydantic 校验或序列化上下文）。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    @model_serializer(mode="wrap")
    def custom_model_dump(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Dict[str, Any]:
        """For full backward compatibility with the text field, we customize the model serializer."""
        # 【中文研读】处理流程：调用父类序列化，再检查 exclude，未排除 text 时补正文。
        data = super().custom_model_dump(handler, info)
        exclude_set = set(info.exclude or [])
        if "text" not in exclude_set:
            data["text"] = self.text
        return data

    # 【中文研读】方法职责：通过统一 get_content 读取文档文本，兼容旧属性访问。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def text(self) -> str:
        """Provided for backward compatibility, it returns the content of text_resource."""
        # 【中文研读】处理流程：通过统一 get_content 读取文档文本，兼容旧属性访问。
        return self.get_content()

    # 【中文研读】方法职责：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def get_type(cls) -> str:
        """Get Document type."""
        # 【中文研读】处理流程：返回节点类别；抽象基类要求子类定义，具体节点用 ObjectType 指明文本、文档或其他种类。
        return ObjectType.DOCUMENT

    # 【中文研读】方法职责：读取并返回 self.id_；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @property
    def doc_id(self) -> str:
        """Get document ID."""
        # 【中文研读】处理流程：读取并返回 self.id_；此入口不发起检索或存储写入。
        return self.id_

    # 【中文研读】方法职责：将传入值 id_ 写入 self.id_；修改的是当前对象属性。
    # 【中文研读】输入参数：id_（节点唯一标识）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @doc_id.setter
    def doc_id(self, id_: str) -> None:
        # 【中文研读】处理流程：将传入值 id_ 写入 self.id_；修改的是当前对象属性。
        self.id_ = id_

    # 【中文研读】方法职责：输出文档 ID 与截断换行后的正文摘要，适合调试展示。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def __str__(self) -> str:
        # 【中文研读】处理流程：输出文档 ID 与截断换行后的正文摘要，适合调试展示。
        source_text_truncated = truncate_text(
            self.get_content().strip(), TRUNCATE_LENGTH
        )
        source_text_wrapped = textwrap.fill(
            f"Text: {source_text_truncated}\n", width=WRAP_WIDTH
        )
        return f"Doc ID: {self.doc_id}\n{source_text_wrapped}"

    # 【中文研读】方法职责：读取并返回 self.id_；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @deprecated(
        version="0.12.2",
        reason="'get_doc_id' is deprecated, access the 'id_' property instead.",
    )
    def get_doc_id(self) -> str:  # pragma: nocover
        # 【中文研读】处理流程：读取并返回 self.id_；此入口不发起检索或存储写入。
        return self.id_

    # 【中文研读】方法职责：转换为 LangChain Document
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：LCDocument。结果的业务含义与失败分支见下面处理流程。
    def to_langchain_format(self) -> LCDocument:
        """Convert struct to LangChain document format."""
        # 【中文研读】处理流程：对应映射正文、metadata 和 ID，不连带 P3 权限语义。
        from llama_index.core.bridge.langchain import (
            Document as LCDocument,  # type: ignore
        )

        metadata = self.metadata or {}
        return LCDocument(page_content=self.text, metadata=metadata, id=self.id_)

    # 【中文研读】方法职责：从 LangChain Document 恢复
    # 【中文研读】输入参数：doc（其他框架的文档对象）。
    # 【中文研读】返回约定：Document。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_langchain_format(cls, doc: LCDocument) -> Document:
        """Convert struct from LangChain document format."""
        # 【中文研读】处理流程：有 ID 时保留，否则由当前 Document 构造规则生成。
        if doc.id:
            return cls(text=doc.page_content, metadata=doc.metadata, id_=doc.id)
        return cls(text=doc.page_content, metadata=doc.metadata)

    # 【中文研读】方法职责：转换为 Haystack Document
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：HaystackDocument。结果的业务含义与失败分支见下面处理流程。
    def to_haystack_format(self) -> HaystackDocument:
        """Convert struct to Haystack document format."""
        # 【中文研读】处理流程：映射正文、metadata、向量和 ID，仅适配数据结构。
        from haystack import Document as HaystackDocument  # type: ignore

        return HaystackDocument(
            content=self.text, meta=self.metadata, embedding=self.embedding, id=self.id_
        )

    # 【中文研读】方法职责：按字段对应关系恢复 Haystack 文档内容、元信息、向量与标识。
    # 【中文研读】输入参数：doc（其他框架的文档对象）。
    # 【中文研读】返回约定：Document。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_haystack_format(cls, doc: HaystackDocument) -> Document:
        """Convert struct from Haystack document format."""
        # 【中文研读】处理流程：按字段对应关系恢复 Haystack 文档内容、元信息、向量与标识。
        return cls(
            text=doc.content, metadata=doc.meta, embedding=doc.embedding, id_=doc.id
        )

    # 【中文研读】方法职责：输出 Embedchain 所需嵌套字典，保留文档 ID 与正文及元信息。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Dict[str, Any]。结果的业务含义与失败分支见下面处理流程。
    def to_embedchain_format(self) -> Dict[str, Any]:
        """Convert struct to EmbedChain document format."""
        # 【中文研读】处理流程：输出 Embedchain 所需嵌套字典，保留文档 ID 与正文及元信息。
        return {
            "doc_id": self.id_,
            "data": {"content": self.text, "meta_data": self.metadata},
        }

    # 【中文研读】方法职责：读取嵌套字典中的正文、元信息和 ID，构造本地 Document。
    # 【中文研读】输入参数：doc（其他框架的文档对象）。
    # 【中文研读】返回约定：Document。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_embedchain_format(cls, doc: Dict[str, Any]) -> Document:
        """Convert struct from EmbedChain document format."""
        # 【中文研读】处理流程：读取嵌套字典中的正文、元信息和 ID，构造本地 Document。
        return cls(
            text=doc["data"]["content"],
            metadata=doc["data"]["meta_data"],
            id_=doc["doc_id"],
        )

    # 【中文研读】方法职责：转换为 MemoryRecord
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：MemoryRecord。结果的业务含义与失败分支见下面处理流程。
    def to_semantic_kernel_format(self) -> MemoryRecord:
        """Convert struct to Semantic Kernel document format."""
        # 【中文研读】处理流程：metadata 格式化为文本，向量如存在则转 numpy 数组；转换可能改变元信息结构。
        import numpy as np
        from semantic_kernel.memory.memory_record import MemoryRecord  # type: ignore

        return MemoryRecord(
            id=self.id_,
            text=self.text,
            additional_metadata=self.get_metadata_str(),
            embedding=np.array(self.embedding) if self.embedding else None,
        )

    # 【中文研读】方法职责：从 MemoryRecord 恢复字段
    # 【中文研读】输入参数：doc（其他框架的文档对象）。
    # 【中文研读】返回约定：Document。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_semantic_kernel_format(cls, doc: MemoryRecord) -> Document:
        """Convert struct from Semantic Kernel document format."""
        # 【中文研读】处理流程：数组向量转 list，附加元信息放入单独键，非完全可逆的结构映射。
        return cls(
            text=doc._text,
            metadata={"additional_metadata": doc._additional_metadata},
            embedding=doc._embedding.tolist() if doc._embedding is not None else None,
            id_=doc._id,
        )

    # 【中文研读】方法职责：把正文写临时文件后交给外部客户端编码
    # 【中文研读】输入参数：client（外部服务客户端）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def to_vectorflow(self, client: Any) -> None:
        """Send a document to vectorflow, since they don't have a document object."""
        # write document to temp file
        # 【中文研读】处理流程：写字节并 flush，再调用 client.embed，包含外部副作用。
        import tempfile

        with tempfile.NamedTemporaryFile() as f:
            f.write(self.text.encode("utf-8"))
            f.flush()
            client.embed(f.name)

    # 【中文研读】方法职责：构造示例 Document
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：Document。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def example(cls) -> Document:
        # 【中文研读】处理流程：使用内置样本文本与示例元信息，不读取真实业务数据。
        return Document(
            text=SAMPLE_TEXT,
            metadata={"filename": "README.md", "category": "codebase"},
        )

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "Document"

    # 【中文研读】方法职责：映射为云端文档对象，包含正文、ID、metadata 及两类元信息排除键；仅构造数据对象。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：CloudDocument。结果的业务含义与失败分支见下面处理流程。
    def to_cloud_document(self) -> CloudDocument:
        """Deprecated: Convert to LlamaCloud document type with <1.0 llama-cloud SDK."""
        # 【中文研读】处理流程：映射为云端文档对象，包含正文、ID、metadata 及两类元信息排除键；仅构造数据对象。
        from llama_cloud.types.cloud_document import CloudDocument  # type: ignore

        return CloudDocument(
            text=self.text,
            metadata=self.metadata,
            excluded_embed_metadata_keys=self.excluded_embed_metadata_keys,
            excluded_llm_metadata_keys=self.excluded_llm_metadata_keys,
            id=self.id_,
        )

    # 【中文研读】方法职责：从云文档结构恢复同名字段与 ID，返回本地 Document。
    # 【中文研读】输入参数：doc（其他框架的文档对象）。
    # 【中文研读】返回约定：Document。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def from_cloud_document(
        cls,
        doc: CloudDocument,
    ) -> Document:
        """Convert from LlamaCloud document type."""
        # 【中文研读】处理流程：从云文档结构恢复同名字段与 ID，返回本地 Document。
        return Document(
            text=doc.text,
            metadata=doc.metadata,
            excluded_embed_metadata_keys=doc.excluded_embed_metadata_keys,
            excluded_llm_metadata_keys=doc.excluded_llm_metadata_keys,
            id_=doc.id,
        )


# 【中文研读】方法职责：验证本地文件能否被 PIL 识别为图像
# 【中文研读】输入参数：file_path（本地图片文件路径）。
# 【中文研读】返回约定：bool。结果的业务含义与失败分支见下面处理流程。
def is_image_pil(file_path: str) -> bool:
    # 【中文研读】处理流程：打开并 verify，IO 或格式错误返回 False。
    try:
        with Image.open(file_path) as img:
            img.verify()  # Verify it's a valid image
        return True
    except (IOError, SyntaxError):
        return False


# 【中文研读】方法职责：联网读取 URL 并验证图像格式
# 【中文研读】输入参数：url（图片网络地址）。
# 【中文研读】返回约定：bool。结果的业务含义与失败分支见下面处理流程。
def is_image_url_pil(url: str) -> bool:
    # 【中文研读】处理流程：请求成功后包装字节交给 PIL，网络或图像错误返回 False；此函数不是 URL 访问授权器。
    try:
        response = requests.get(url, stream=True, timeout=(60, 60))
        response.raise_for_status()  # Raise an exception for bad status codes
        # Open image from the response content
        img = Image.open(BytesIO(response.content))
        img.verify()
        return True
    except (requests.RequestException, IOError, SyntaxError):
        return False


# 【中文研读】类型职责：文档多模态结构的兼容包装；把旧 image/image_path 等参数转为 MediaResource。
class ImageDocument(Document):
    """Backward compatible wrapper around Document containing an image."""

    # 【中文研读】方法职责：把旧图像参数转为资源
    # 【中文研读】输入参数：**kwargs（透传选项）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    def __init__(self, **kwargs: Any) -> None:
        # 【中文研读】处理流程：优先内嵌图像，否则验证路径或 URL，再交给父类构造；URL 分支可能在构造时联网。
        image = kwargs.pop("image", None)
        image_path = kwargs.pop("image_path", None)
        image_url = kwargs.pop("image_url", None)
        image_mimetype = kwargs.pop("image_mimetype", None)
        text_embedding = kwargs.pop("text_embedding", None)

        if image:
            kwargs["image_resource"] = MediaResource(
                data=image, mimetype=image_mimetype
            )
        elif image_path:
            if not is_image_pil(image_path):
                raise ValueError("The specified file path is not an accessible image")
            kwargs["image_resource"] = MediaResource(
                path=image_path, mimetype=image_mimetype
            )
        elif image_url:
            if not is_image_url_pil(image_url):
                raise ValueError("The specified URL is not an accessible image")
            kwargs["image_resource"] = MediaResource(
                url=image_url, mimetype=image_mimetype
            )

        super().__init__(**kwargs)

    # 【中文研读】方法职责：图像数据兼容属性
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str | None。结果的业务含义与失败分支见下面处理流程。
    @property
    def image(self) -> str | None:
        # 【中文研读】处理流程：读取时需存在内嵌数据并解码为字符串，写入时编码字符串并替换 image_resource。
        if self.image_resource and self.image_resource.data:
            return self.image_resource.data.decode("utf-8")
        return None

    # 【中文研读】方法职责：图像数据兼容属性
    # 【中文研读】输入参数：image（图像内容字符串）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @image.setter
    def image(self, image: str) -> None:
        # 【中文研读】处理流程：读取时需存在内嵌数据并解码为字符串，写入时编码字符串并替换 image_resource。
        self.image_resource = MediaResource(data=image.encode("utf-8"))

    # 【中文研读】方法职责：图像路径兼容属性
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str | None。结果的业务含义与失败分支见下面处理流程。
    @property
    def image_path(self) -> str | None:
        # 【中文研读】处理流程：存在时转字符串返回，写入则构造带 Path 的新资源。
        if self.image_resource and self.image_resource.path:
            return str(self.image_resource.path)
        return None

    # 【中文研读】方法职责：图像路径兼容属性
    # 【中文研读】输入参数：image_path（图像文件路径）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @image_path.setter
    def image_path(self, image_path: str) -> None:
        # 【中文研读】处理流程：存在时转字符串返回，写入则构造带 Path 的新资源。
        self.image_resource = MediaResource(path=Path(image_path))

    # 【中文研读】方法职责：图像 URL 兼容属性
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str | None。结果的业务含义与失败分支见下面处理流程。
    @property
    def image_url(self) -> str | None:
        # 【中文研读】处理流程：读取时返回字符串，写入时用 AnyUrl 包装并替换资源。
        if self.image_resource and self.image_resource.url:
            return str(self.image_resource.url)
        return None

    # 【中文研读】方法职责：图像 URL 兼容属性
    # 【中文研读】输入参数：image_url（图像网络地址）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @image_url.setter
    def image_url(self, image_url: str) -> None:
        # 【中文研读】处理流程：读取时返回字符串，写入时用 AnyUrl 包装并替换资源。
        self.image_resource = MediaResource(url=AnyUrl(url=image_url))

    # 【中文研读】方法职责：图像 MIME 属性
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str | None。结果的业务含义与失败分支见下面处理流程。
    @property
    def image_mimetype(self) -> str | None:
        # 【中文研读】处理流程：读取已有资源类型，写入时仅在资源存在时更新，不自动创建图像。
        if self.image_resource:
            return self.image_resource.mimetype
        return None

    # 【中文研读】方法职责：图像 MIME 属性
    # 【中文研读】输入参数：image_mimetype（图像 MIME 类型）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @image_mimetype.setter
    def image_mimetype(self, image_mimetype: str) -> None:
        # 【中文研读】处理流程：读取已有资源类型，写入时仅在资源存在时更新，不自动创建图像。
        if self.image_resource:
            self.image_resource.mimetype = image_mimetype

    # 【中文研读】方法职责：文本稠密向量的兼容属性
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：list[float] | None。结果的业务含义与失败分支见下面处理流程。
    @property
    def text_embedding(self) -> list[float] | None:
        # 【中文研读】处理流程：读取 text_resource.embeddings 中的 dense，写入时先补向量字典再设置。
        if self.text_resource and self.text_resource.embeddings:
            return self.text_resource.embeddings.get("dense")
        return None

    # 【中文研读】方法职责：文本稠密向量的兼容属性
    # 【中文研读】输入参数：embeddings（待设置的文本向量）。
    # 【中文研读】返回约定：None。结果的业务含义与失败分支见下面处理流程。
    @text_embedding.setter
    def text_embedding(self, embeddings: list[float]) -> None:
        # 【中文研读】处理流程：读取 text_resource.embeddings 中的 dense，写入时先补向量字典再设置。
        if self.text_resource:
            if self.text_resource.embeddings is None:
                self.text_resource.embeddings = {}
            self.text_resource.embeddings["dense"] = embeddings

    # 【中文研读】方法职责：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    @classmethod
    def class_name(cls) -> str:
        # 【中文研读】处理流程：返回序列化使用的组件类型标记；它用于区分对象类型，不是业务对象 ID。
        return "ImageDocument"

    # 【中文研读】方法职责：统一得到图像字节流
    # 【中文研读】输入参数：as_base64（是否返回 base64 编码字节）。
    # 【中文研读】返回约定：BytesIO。结果的业务含义与失败分支见下面处理流程。
    def resolve_image(self, as_base64: bool = False) -> BytesIO:
        """
        Resolve an image such that PIL can read it.

        Args:
            as_base64 (bool): whether the resolved image should be returned as base64-encoded bytes

        """
        # 【中文研读】处理流程：优先内嵌数据，再文件，再 URL，按 as_base64 决定返回编码数据还是原始字节。
        if self.image_resource is None:
            return BytesIO()

        # 【中文研读】优先使用已有内嵌字节，避免重复读取文件或网络。
        if self.image_resource.data is not None:
            if as_base64:
                return BytesIO(self.image_resource.data)
            return BytesIO(base64.b64decode(self.image_resource.data))
        elif self.image_resource.path is not None:
            img_bytes = self.image_resource.path.read_bytes()
            if as_base64:
                return BytesIO(base64.b64encode(img_bytes))
            return BytesIO(img_bytes)
        # 【中文研读】该分支真正发起网络请求；访问控制、超时策略和资源限制需由接入层另行约束。
        elif self.image_resource.url is not None:
            # load image from URL
            response = requests.get(str(self.image_resource.url), timeout=(60, 60))
            img_bytes = response.content
            if as_base64:
                return BytesIO(base64.b64encode(img_bytes))
            return BytesIO(img_bytes)
        else:
            raise ValueError("No image found in the chat message!")


# 【中文研读】类型职责：本次查询输入：原始文本、可选图片、用于编码的文本列表及预计算向量；不含可信业务身份。
@dataclass
class QueryBundle(DataClassJsonMixin):
    """
    Query bundle.

    This dataclass contains the original query string and associated transformations.

    Args:
        query_str (str): the original user-specified query string.
            This is currently used by all non embedding-based queries.
        custom_embedding_strs (list[str]): list of strings used for embedding the query.
            This is currently used by all embedding-based queries.
        embedding (list[float]): the stored embedding for the query.

    """

    # 【中文研读】用户查询文本，保留原问题供检索和后处理参考。
    query_str: str
    # using single image path as query input
    image_path: Optional[str] = None
    # 【中文研读】可用与原问题不同的文本来生成查询向量；需明确改写与原请求之间的关系。
    custom_embedding_strs: Optional[List[str]] = None
    # 【中文研读】预计算查询向量；使用方还需校验模型空间，字段本身只有数值列表。
    embedding: Optional[List[float]] = None

    # 【中文研读】方法职责：决定哪些文本送入 Embedding
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[str]。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding_strs(self) -> List[str]:
        """Use custom embedding strs if specified, otherwise use query str."""
        # 【中文研读】处理流程：有 custom_embedding_strs 就使用它，否则非空查询包装成单元素列表，空查询返回空列表。
        # 【中文研读】没有自定义编码文本时使用原查询；自定义列表即使为空也按原样采用。
        if self.custom_embedding_strs is None:
            if len(self.query_str) == 0:
                return []
            return [self.query_str]
        else:
            return self.custom_embedding_strs

    # 【中文研读】方法职责：把可选查询图片路径包装成列表
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：List[ImageType]。结果的业务含义与失败分支见下面处理流程。
    @property
    def embedding_image(self) -> List[ImageType]:
        """Use image path for image retrieval."""
        # 【中文研读】处理流程：无路径返回空，有路径返回单元素列表。
        if self.image_path is None:
            return []
        return [self.image_path]

    # 【中文研读】方法职责：读取并返回 self.query_str；此入口不发起检索或存储写入。
    # 【中文研读】输入参数：使用对象已有状态。
    # 【中文研读】返回约定：str。结果的业务含义与失败分支见下面处理流程。
    def __str__(self) -> str:
        """Convert to string representation."""
        # 【中文研读】处理流程：读取并返回 self.query_str；此入口不发起检索或存储写入。
        return self.query_str


QueryType = Union[str, QueryBundle]
