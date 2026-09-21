可以。以下流程全部在你的另一台 Windows 电脑上执行，我不会替你运行。

## 一、准备目录和 Docker

打开 PowerShell：

```
cd D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory

docker version
docker info
```

确认 `docker info` 能正常输出，说明 Docker Desktop 已启动。

注意：当前仓库的 `engine/docker/docker-compose.yml` 只是占位服务，不会启动 B1 Sidecar。本次使用 `docker build/run`。

## 二、构建 B1 验证镜像

下面使用临时 Dockerfile，不修改仓库原始文件：

```
@'
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY scripts ./scripts
COPY engine/proto ./engine/proto

RUN python -m pip install --no-cache-dir -e ".[b1-sidecar]" && python scripts/generate_proto.py
'@ | docker build -t aether-b1-verify -f - .
```

构建成功时，最后应看到：

```
Successfully tagged aether-b1-verify:latest
```

创建模型缓存和 Docker 网络：

```
docker volume create aether-b1-model-cache
docker network create aether-b1-net
```

如果提示已经存在，可以忽略。

## 三、下载并验证模型

执行：

```
docker run --rm `
  --name aether-b1-model-download `
  --network aether-b1-net `
  -e AETHER_B1_CACHE_DIR=/app/.aether/b1/models `
  -v aether-b1-model-cache:/app/.aether/b1/models `
  aether-b1-verify `
  python -m aether_agent_memory.b1.model_download `
  --model-name BAAI/bge-small-zh-v1.5 `
  --cache-dir /app/.aether/b1/models `
  --threads 1
```

成功判据：

```
{
  "status": "downloaded_and_verified",
  "model": "BAAI/bge-small-zh-v1.5",
  "dimension": 512
}
```

并且 `runtime.provider` 或运行时信息中应出现：

```
CPUExecutionProvider
```

## 四、启动 B1 Sidecar

执行：

```
docker run -d `
  --name aether-b1-sidecar `
  --network aether-b1-net `
  --network-alias b1-sidecar `
  -p 18081:18081 `
  -e AETHER_B1_HOST=0.0.0.0 `
  -e AETHER_B1_PORT=18081 `
  -e AETHER_B1_BACKEND=onnx `
  -e AETHER_B1_MODEL_NAME=BAAI/bge-small-zh-v1.5 `
  -e AETHER_B1_CACHE_DIR=/app/.aether/b1/models `
  -e AETHER_B1_THREADS=1 `
  -e AETHER_B1_MODEL_BATCH_SIZE=8 `
  -e AETHER_B1_MAX_CONCURRENCY=1 `
  -e AETHER_B1_FAIL_MODE=open `
  -e AETHER_B1_EAGER_LOAD=true `
  -v aether-b1-model-cache:/app/.aether/b1/models `
  aether-b1-verify `
  python -m aether_agent_memory.b1.sidecar
```

查看容器状态：

```
docker ps --filter "name=aether-b1-sidecar"
docker logs --tail 100 aether-b1-sidecar
```

如果容器没有运行：

```
docker ps -a --filter "name=aether-b1-sidecar"
docker logs aether-b1-sidecar
```

模型第一次加载可能需要几十秒。

## 五、验证健康状态

执行：

```
Invoke-RestMethod http://127.0.0.1:18081/health/live |
  ConvertTo-Json -Depth 5
```

预期：

```
{
  "status": "alive",
  "module": "P3-B1"
}
```

检查模型是否就绪：

```
Invoke-RestMethod http://127.0.0.1:18081/health/ready |
  ConvertTo-Json -Depth 10
```

必须看到：

```
{
  "status": "ready",
  "dimension": 512,
  "provider": "CPUExecutionProvider",
  "load_error": null
}
```

如果出现 `503`，查看：

```
docker logs aether-b1-sidecar
```

## 六、验证拦截并返回真实向量

在 PowerShell 中构造请求：

```
$payload = @{
  items = @(
    @{
      request_id = "verify-normal-001"
      trace_id = "trace-normal-001"
      tenant_id = "tenant-demo"
      source_type = "rag_document"
      source_id = "doc-001"
      object_id = "object-001"
      chunk_id = "input-chunk-001"
      chunk_text = "这是一段真实发送给 B1 Sidecar 的中文文本，用于验证拦截、模型推理和向量返回。"
      embedding_required = $true
      input_type = "passage"
      metadata = @{
        namespace = "b1-verification"
      }
    }
  )
} | ConvertTo-Json -Depth 10

$response = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:18081/v1/intercept `
  -ContentType "application/json" `
  -Body $payload

$item = $response.results[0]

$l2 = [math]::Sqrt(
  (($item.vector | ForEach-Object {
    [double]$_ * [double]$_
  } | Measure-Object -Sum).Sum)
)

[pscustomobject]@{
  overall_status = $response.overall_status
  item_status = $item.status
  input_chunk_id = "input-chunk-001"
  output_chunk_id = $item.chunk_id
  backend = $item.backend
  model = $item.embedding_model
  embedding_dim = $item.embedding_dim
  vector_count = $item.vector_count
  normalized = $item.normalized
  l2_norm = $l2
  first_8_values = (($item.vector | Select-Object -First 8) -join ", ")
}
```

成功判据：

```
overall_status : success
item_status    : success
embedding_dim  : 512
vector_count   : 1
normalized     : True
l2_norm        : 接近 1.0
first_8_values : 一组非零浮点数
```

这一步就已经证明：

```
应用请求
  -> /v1/intercept
  -> B1 ONNX CPU 模型
  -> 返回真实 512 维向量
```

## 七、验证长文本、空文本和跳过逻辑

```
$longText = ("这是一段用于测试长文本分块的内容，B1 会根据最大字符数和重叠字符数进行切分。 " * 120)

$batch = @{
  items = @(
    @{
      request_id = "verify-long-001"
      trace_id = "trace-long-001"
      tenant_id = "tenant-demo"
      source_type = "rag_document"
      source_id = "doc-long"
      object_id = "object-long"
      chunk_id = "input-long-001"
      chunk_text = $longText
      embedding_required = $true
      input_type = "passage"
      metadata = @{}
    },
    @{
      request_id = "verify-empty-001"
      trace_id = "trace-empty-001"
      tenant_id = "tenant-demo"
      source_type = "rag_document"
      source_id = "doc-empty"
      object_id = "object-empty"
      chunk_id = "input-empty-001"
      chunk_text = "   "
      embedding_required = $true
      input_type = "passage"
      metadata = @{}
    },
    @{
      request_id = "verify-skip-001"
      trace_id = "trace-skip-001"
      tenant_id = "tenant-demo"
      source_type = "rag_document"
      source_id = "doc-skip"
      object_id = "object-skip"
      chunk_id = "input-skip-001"
      chunk_text = "不需要生成向量的文本"
      embedding_required = $false
      input_type = "passage"
      metadata = @{}
    }
  )
} | ConvertTo-Json -Depth 10

$batchResponse = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:18081/v1/intercept `
  -ContentType "application/json" `
  -Body $batch

$batchResponse.results | ForEach-Object {
  [pscustomobject]@{
    request_id = $_.request_id
    status = $_.status
    error_code = $_.error_code
    chunk_count = $_.chunk_count
    vector_count = $_.vector_count
    embedding_dim = $_.embedding_dim
    output_chunk_ids = (($_.chunks | ForEach-Object { $_.chunk_id }) -join ",")
  }
}
```

预期：

| 场景                       | 预期结果                                            |
| -------------------------- | --------------------------------------------------- |
| 长文本                     | `success`，`chunk_count > 1`，生成多个输出 chunk ID |
| 空文本                     | `failed`，错误码通常为 `B1_EMPTY_TEXT`              |
| `embedding_required=false` | `skipped`，不返回向量                               |

## 八、验证幂等

同一个请求连续提交两次：

```
$idemPayload = @{
  items = @(
    @{
      request_id = "verify-idempotent-001"
      trace_id = "trace-idempotent-001"
      tenant_id = "tenant-demo"
      source_type = "rag_document"
      source_id = "doc-idempotent"
      object_id = "object-idempotent"
      chunk_id = "input-idempotent-001"
      chunk_text = "幂等请求测试文本"
      embedding_required = $true
      input_type = "passage"
      metadata = @{}
    }
  )
} | ConvertTo-Json -Depth 10

$first = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:18081/v1/intercept `
  -ContentType "application/json" `
  -Body $idemPayload

$second = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:18081/v1/intercept `
  -ContentType "application/json" `
  -Body $idemPayload

$second.results[0] |
  Select-Object request_id, status, idempotent_replay, chunk_id
```

预期：

```
status             : success
idempotent_replay  : True
```

## 九、查看实时指标和流程事件

```
Invoke-RestMethod http://127.0.0.1:18081/metrics |
  ConvertTo-Json -Depth 10
```

重点查看：

```
requests
items
success
failed
skipped
total_chunks
total_vectors
requests_per_second
items_per_second
request_latency_p50_ms
request_latency_p95_ms
request_latency_p99_ms
error_codes
```

查看最近流程：

```
Invoke-RestMethod "http://127.0.0.1:18081/v1/events?limit=20" |
  ConvertTo-Json -Depth 10
```

可以看到类似：

```
INTERCEPT
VALIDATE
CHUNK
CPU EMBED
L2 NORMALIZE
RETURN
```

这里的 QPS 是最近窗口的实时观测值，不是正式压力测试结果。

查看能力和 SIMD：

```
Invoke-RestMethod http://127.0.0.1:18081/v1/capabilities |
  ConvertTo-Json -Depth 20
```

当前应显示：

```
current_backend = onnx
engine = FastEmbed + ONNX Runtime CPUExecutionProvider
research_implemented = scalar, explicit-avx2
reserved = explicit-avx512, amx-bf16, amx-int8
```

这不代表当前 Sidecar 已经自动使用 AVX-512 或 AMX。

## 十、验证现有 P3Runtime 是否选择新 Sidecar

执行：

```
@'
import os
from pathlib import Path

os.environ["AETHER_B1_SIDECAR_URL"] = "http://127.0.0.1:18081"

from aether_agent_memory.runtime import P3Runtime, P3RuntimeConfig

runtime = P3Runtime(
    P3RuntimeConfig(
        p2_endpoint="unused-p2:50052",
        data_dir=Path("/tmp/b1-runtime-check"),
    )
)

print("embedder =", type(runtime.embedder).__name__)
print("embedding_path =", runtime.embedding_path)
print("embedding_model =", runtime.embedding_model)
'@ | docker exec -i aether-b1-sidecar python -
```

预期：

```
embedder = SidecarEmbeddingClient
embedding_path = B1 Sidecar -> EmbeddingPipeline -> P2VectorSink
embedding_model = BAAI/bge-small-zh-v1.5
```

这一步只验证选择逻辑，不会连接真实 P2。

## 十一、验证 Sidecar 返回值继续进入原有 Pipeline

下面使用内存 Fake Sink 代替真实 P2，验证原有 `EmbeddingPipeline` 是否能接收 Sidecar 向量：

```
@'
import asyncio

from aether_agent_memory.b1.pipeline import EmbeddingPipeline, EmbeddingRequest, TextChunker
from aether_agent_memory.b1.sidecar_client import SidecarEmbeddingClient
from aether_agent_memory.core.enums import SourceType

class FakeSink:
    def __init__(self):
        self.records = []

    async def upsert(self, records):
        self.records.extend(records)

async def main():
    sink = FakeSink()
    client = SidecarEmbeddingClient("http://127.0.0.1:18081")

    pipeline = EmbeddingPipeline(
        embedder=client,
        sink=sink,
        chunker=TextChunker(max_chars=400, overlap_chars=40),
        model_name="BAAI/bge-small-zh-v1.5",
    )

    request = EmbeddingRequest(
        request_id="pipeline-verify-001",
        trace_id="pipeline-trace-001",
        source_type=SourceType.DOCUMENT,
        source_id="doc-pipeline-001",
        object_id="object-pipeline-001",
        text=("这是一段较长文本，用于确认 Pipeline 会先分块，再调用 Sidecar，最后把 EmbeddingRecord 写入 Sink。 " * 80),
        metadata={"verification": "b1-sidecar"},
    )

    result = await pipeline.process(request)

    print({
        "status": result.status.value,
        "pipeline_records": len(result.records),
        "sink_records": len(sink.records),
        "first_chunk_id": sink.records[0].chunk_id if sink.records else None,
        "vector_dimension": len(sink.records[0].vector) if sink.records else None,
        "embedding_model": sink.records[0].embedding_model if sink.records else None,
    })

    await client.close()

asyncio.run(main())
'@ | docker exec -i aether-b1-sidecar python -
```

成功判据：

```
status             : success
pipeline_records   : 大于 0
sink_records       : 与 pipeline_records 相同
vector_dimension   : 512
embedding_model    : BAAI/bge-small-zh-v1.5
```

这证明：

```
B1 Sidecar
  -> SidecarEmbeddingClient
  -> 原有 EmbeddingPipeline
  -> EmbeddingRecord
  -> Fake P2 Sink
```

真实 P2 没有启动时，不能声称已经完成 P2 E1 持久化写入。

## 十二、验证 Sidecar 传输异常

先停止 Sidecar：

```
docker stop aether-b1-sidecar
```

执行：

```
@'
import asyncio
from aether_agent_memory.b1.sidecar_client import SidecarEmbeddingClient

async def main():
    client = SidecarEmbeddingClient(
        "http://host.docker.internal:18081",
        timeout_seconds=3,
    )
    try:
        await client.embed(["Sidecar down transport test"])
        print("unexpected success")
    except Exception as exc:
        print(type(exc).__name__)
        print(str(exc))
    finally:
        await client.close()

asyncio.run(main())
'@ | docker run --rm -i `
  --add-host=host.docker.internal:host-gateway `
  aether-b1-verify `
  python -
```

预期：

```
SidecarEmbeddingError
B1 Sidecar readiness request failed ...
```

这说明网络传输失败时不会生成伪造向量。

恢复 Sidecar：

```
docker start aether-b1-sidecar
```

重新等待 `/health/ready` 返回 `status=ready`。

## 十三、关于 P3 `/health` 的判断

如果你启动 P3：

```
docker run --rm `
  --name aether-p3 `
  --network aether-b1-net `
  -p 8080:8080 `
  -e AETHER_B1_SIDECAR_URL=http://b1-sidecar:18081 `
  -e AETHER_P2_GRPC=localhost:50052 `
  aether-b1-verify `
  python scripts/p3_service.py
```

另开 PowerShell：

```
Invoke-RestMethod http://127.0.0.1:8080/health |
  ConvertTo-Json -Depth 10
```

在没有真实 P2 时：

```
p2_online = false
```

是预期结果，不代表 B1 Sidecar 失败。

B1 的验收依据应是：

1. `/health/ready` 返回 `ready`。
2. `/v1/intercept` 返回真实 512 维向量。
3. 向量 L2 范数接近 `1.0`。
4. 长文本产生多个 chunk。
5. 空文本和错误请求返回结构化失败。
6. 幂等请求返回 `idempotent_replay=true`。
7. `P3Runtime` 使用 `SidecarEmbeddingClient`。
8. 原有 Pipeline 能将结果写入 Fake Sink。
9. Sidecar 停止时客户端返回结构化异常。

清理容器：

```
docker rm -f aether-b1-sidecar aether-p3
```

模型缓存和网络可以保留，方便下次验证。