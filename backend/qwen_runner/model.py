"""Qwen3-VL-8B-Instruct on one CUDA GPU, loaded once and kept resident.

Default: the BF16 base model (pilot arm B: cached BF16 + revised prompt v2).
Optional: QWEN_LORA_DIR loads the experimental pilot adapter on the NF4
language model (pilot arm D). Weights must already be on disk; no downloads.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from qwen_runner.windows import messages_for

MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct"
REVISION = "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"
MAX_INPUT_TOKENS = 8192


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class QwenModel:
    def __init__(self, model_dir: str, lora_dir: str | None = None, max_new_tokens: int = 1024):
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA required; no CPU fallback")
        self.torch = torch
        path = Path(model_dir).resolve(strict=True)
        self.processor = AutoProcessor.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        # Batched generation pads on the left so every answer starts at the same column.
        self.processor.tokenizer.padding_side = "left"
        kwargs = dict(local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16,
                      device_map={"": 0}, low_cpu_mem_usage=True, attn_implementation="sdpa")
        self.lora_sha = None
        if lora_dir:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16, llm_int8_skip_modules=["visual", "lm_head"])
        model = Qwen3VLForConditionalGeneration.from_pretrained(str(path), **kwargs)
        if lora_dir:
            from peft import PeftModel
            adapter = Path(lora_dir).resolve(strict=True)
            self.lora_sha = file_sha(adapter / "adapter_model.safetensors")
            model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False)
        self.model = model.eval()
        self.max_new_tokens = max_new_tokens
        self.device_name = torch.cuda.get_device_name(0)

    @property
    def version(self) -> str:
        """Must equal QWEN_MODEL_VERSION in the backend settings."""
        tag = f"{REVISION[:12]}+prompt-v2"
        return tag + (f"+lora-{self.lora_sha[:12]}" if self.lora_sha else "")

    def generate(self, windows: list[dict], prompt: str, deadline: float) -> list[str | None]:
        """Raw text for each window, in order; None when out of time."""
        return self.generate_conversations([messages_for(window, prompt) for window in windows], deadline)

    def generate_conversations(self, conversations: list[list[dict]], deadline: float) -> list[str | None]:
        """One padded batch of chat conversations → raw answers; None when out of time."""
        torch = self.torch
        inputs = self.processor.apply_chat_template(
            conversations, add_generation_prompt=True, tokenize=True, return_dict=True,
            return_tensors="pt", padding=True).to(self.model.device)
        lengths = inputs["attention_mask"].sum(dim=1).tolist()
        if max(lengths) > MAX_INPUT_TOKENS:
            raise ValueError(f"Input context {max(lengths)} exceeds {MAX_INPUT_TOKENS} tokens")
        remaining = deadline - time.monotonic()
        if remaining < 5:
            return [None] * len(conversations)
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens, do_sample=False,
                                         use_cache=True, max_time=remaining)
        prompt_len = inputs["input_ids"].shape[-1]
        return self.processor.batch_decode(output[:, prompt_len:], skip_special_tokens=True)
