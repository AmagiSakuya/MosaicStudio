"""ONNX 动漫画面分级模型推理封装。

模型：deepghs/anime_rating（MIT）
  - nano : mobilenetv3_sce_dist  （约 17MB，0.63 GFLOPs，速度快）
  - pro  : caformer_s36_plus     （约 150MB，22.1 GFLOPs，准确率更高）
输出 3 类概率：safe / r15 / r18
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .gpu import cuda_runtime_ok, prepare_cuda_dlls

# 必须在 import onnxruntime 之前把 CUDA DLL 目录加好，否则 CUDA provider 加载会失败
_CUDA_DLL_DIRS = prepare_cuda_dlls()

import onnxruntime as ort  # noqa: E402

# ImageNet 归一化参数（两个模型都基于 timm 的 ImageNet 预训练权重导出）
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# variant -> (子目录, 兜底输入尺寸)
VARIANTS: dict[str, tuple[str, int]] = {
    "nano": ("mobilenetv3_sce", 224),
    "pro": ("caformer_s36_plus", 384),
    "ultra": ("caformer_b36", 384),
}


@dataclass
class ModelInfo:
    variant: str
    subdir: str
    path: Path
    input_size: tuple[int, int]  # (h, w)
    labels: list[str]
    provider: str

    @property
    def device(self) -> str:
        if self.provider.startswith("CUDA"):
            return "GPU"
        if self.provider.startswith("Dml"):
            return "GPU (DirectML)"
        return "CPU"

    def as_dict(self) -> dict:
        return {
            "variant": self.variant,
            "name": f"deepghs/anime_rating/{self.subdir}",
            "input_size": list(self.input_size),
            "labels": self.labels,
            "provider": self.provider,
            "device": self.device,
        }


class RatingDetector:
    """把视频帧批量送进 ONNX 模型，得到每帧的 safe/r15/r18 概率。"""

    def __init__(
        self,
        model_dir: Path,
        variant: str = "nano",
        threads: int = 0,
        fit: str = "crop",
        device: str = "auto",
    ) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"未知模型档位：{variant}，可选 {list(VARIANTS)}")
        subdir, fallback = VARIANTS[variant]
        model_path = Path(model_dir) / "anime_rating" / subdir / "model.onnx"
        if not model_path.is_file():
            raise FileNotFoundError(
                f"模型文件缺失：{model_path}\n"
                "请点「模型配置」右上角的齿轮，在「运行时管理」里下载该档位的模型。"
            )

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # 只保留 fatal 日志：CUDA 不可用时会自己回退 CPU，由我们给出中文提示，不必刷一屏英文报错
        so.log_severity_level = 4
        if threads and threads > 0:
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1

        self.session = self._open_session(model_path, so, device)
        self.info = ModelInfo(
            variant=variant,
            subdir=subdir,
            path=model_path,
            input_size=self._resolve_input_size(fallback),
            labels=self._load_labels(model_path.parent),
            provider=self.session.get_providers()[0],
        )
        self.input_name = self.session.get_inputs()[0].name
        self.fit = fit

    def _open_session(self, model_path: Path, so, device: str):
        """优先用 CUDA，其次 DirectML，最后 CPU。指定设备不可用时会明确报错。"""
        available = ort.get_available_providers()
        has_cuda = "CUDAExecutionProvider" in available
        has_dml = "DmlExecutionProvider" in available
        runtime_ok, reason = cuda_runtime_ok() if has_cuda else (False, "当前推理引擎是 CPU 版")

        if device == "cuda":
            if not has_cuda:
                raise RuntimeError(
                    "当前推理引擎没有 CUDA 支持。\n"
                    "请在「模型配置」右上角的齿轮里打开「运行时管理」→ 安装 GPU 版引擎。\n"
                    "（需要 NVIDIA 显卡 + CUDA 13 / cuDNN 9 运行库）"
                )
            if not runtime_ok:
                raise RuntimeError(
                    f"CUDA 运行库不完整：{reason}\n"
                    "请在「运行时管理 → 运行环境」里点「下载 / 修复运行库」。"
                )
        elif device == "dml":
            if not has_dml:
                raise RuntimeError(
                    "当前推理引擎没有 DirectML 支持。\n"
                    "请在「运行时管理 → 运行环境」里安装 DirectML 版引擎（适用于 AMD / Intel 显卡）。"
                )

        if device == "dml":
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        elif has_cuda and (device in ("auto", "cuda")) and runtime_ok:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif device == "auto" and has_dml:
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        session = ort.InferenceSession(str(model_path), so, providers=providers)
        used = session.get_providers()

        if device == "cuda" and "CUDAExecutionProvider" not in used:
            raise RuntimeError(
                "CUDA 初始化失败。\n"
                "常见原因：缺少 CUDA 13 / cuDNN 9 运行库，或显卡驱动过旧。\n"
                "可改用「设备」里的 CPU，或在运行时管理里修复运行库。"
            )
        if device == "dml" and "DmlExecutionProvider" not in used:
            raise RuntimeError(
                "DirectML 初始化失败。\n"
                "请更新显卡驱动（AMD Adrenalin / Intel 驱动）后重试，或改用 CPU。"
            )
        return session

    # ---------- 初始化辅助 ----------
    def _resolve_input_size(self, fallback: int) -> tuple[int, int]:
        shape = self.session.get_inputs()[0].shape
        h = shape[2] if len(shape) > 2 and isinstance(shape[2], int) else fallback
        w = shape[3] if len(shape) > 3 and isinstance(shape[3], int) else h
        return int(h), int(w)

    @staticmethod
    def _load_labels(model_dir: Path) -> list[str]:
        meta = model_dir / "meta.json"
        if meta.is_file():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                labels = data.get("labels")
                if isinstance(labels, list) and labels:
                    return [str(x) for x in labels]
            except (OSError, json.JSONDecodeError):
                pass
        return ["safe", "r15", "r18"]

    # ---------- 推理 ----------
    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        h, w = self.info.input_size
        if frame.shape[0] != h or frame.shape[1] != w:
            if self.fit == "crop":
                # 训练时的标准做法：短边缩放 + 中心裁剪
                sh, sw = frame.shape[:2]
                scale = max(h / sh, w / sw)
                frame = cv2.resize(
                    frame, (max(w, int(round(sw * scale))), max(h, int(round(sh * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
                y0 = (frame.shape[0] - h) // 2
                x0 = (frame.shape[1] - w) // 2
                frame = frame[y0:y0 + h, x0:x0 + w]
            else:
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - _MEAN) / _STD
        return rgb.transpose(2, 0, 1)

    def predict(self, frames: list[np.ndarray]) -> np.ndarray:
        """返回 shape=(n, 类别数) 的概率矩阵。"""
        if not frames:
            return np.zeros((0, len(self.info.labels)), dtype=np.float32)

        batch = np.stack([self._prepare(f) for f in frames], axis=0)
        outputs = self.session.run(None, {self.input_name: batch})
        logits = np.asarray(outputs[0], dtype=np.float32)
        if logits.ndim == 4:  # 个别导出版本是 (n, c, 1, 1)
            logits = logits.reshape(logits.shape[0], -1)
        return _to_probs(logits)


def _to_probs(x: np.ndarray) -> np.ndarray:
    """输出如果已经是概率就原样返回，否则做 softmax（兼容不同导出方式）。"""
    if x.min() >= -1e-6 and x.max() <= 1.0 + 1e-6 and np.allclose(x.sum(axis=1), 1.0, atol=1e-3):
        return x
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)
