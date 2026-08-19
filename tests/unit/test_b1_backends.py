from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from aether_agent_memory.b1.backends import BackendConfig, OpenVinoBackend


class FakeTensor:
    def __init__(self, data: np.ndarray) -> None:
        self.data = data


class FakeRequest:
    def __init__(self, data: np.ndarray) -> None:
        self.output_tensors = [FakeTensor(data)]


class FakeAsyncInferQueue:
    def __init__(self, compiled_model: object, jobs: int = 0) -> None:
        self.compiled_model = compiled_model
        self.jobs = jobs
        self.callback: Any = None

    def set_callback(self, callback: Any) -> None:
        self.callback = callback

    def start_async(
        self,
        inputs: dict[str, np.ndarray],
        userdata: Any = None,
        share_inputs: bool = False,
    ) -> None:
        del share_inputs
        assert self.callback is not None
        batch = int(next(iter(inputs.values())).shape[0])
        value = float(int(userdata) + 1)
        hidden = np.full((batch, 1, 4), value, dtype=np.float32)
        self.callback(FakeRequest(hidden), userdata)

    def wait_all(self) -> None:
        return None


@pytest.mark.unit
def test_openvino_async_infer_queue_merges_micro_batches_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "openvino",
        SimpleNamespace(AsyncInferQueue=FakeAsyncInferQueue),
    )
    backend = OpenVinoBackend(
        BackendConfig(
            model_name="fake",
            cache_dir=__import__("pathlib").Path("unused"),
            model_path=None,
            threads=1,
            openvino_async=True,
            openvino_infer_requests=3,
        )
    )
    backend._compiled_model = object()
    backend._async_enabled = True

    vectors = backend._async_infer_batches(
        [
            {"input_ids": np.zeros((2, 4), dtype=np.int64)},
            {"input_ids": np.zeros((1, 4), dtype=np.int64)},
        ]
    )

    assert [float(vector[0]) for vector in vectors] == [1.0, 1.0, 2.0]
    details = backend.runtime_details()
    assert details["async_inference"] is True
    assert details["inference_mode"] == "openvino_async"
