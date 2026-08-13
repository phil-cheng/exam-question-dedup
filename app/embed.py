"""远程 embedding：优先 OpenAI 兼容（vLLM / Ollama /v1），失败再试 Ollama 原生。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import numpy as np

ProgressFn = Callable[[int, int, str], None]


class EmbedError(RuntimeError):
    pass


def _normalize_base(url: str) -> str:
    return url.strip().rstrip("/")


def _openai_url(base: str) -> str:
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    if base.endswith("/embeddings"):
        return base
    return f"{base}/v1/embeddings"


def _ollama_url(base: str) -> str:
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/api/embed"


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


def _parse_openai(payload: Any) -> list[list[float]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise EmbedError("OpenAI 兼容接口未返回 data。")
    ordered = sorted(data, key=lambda x: int(x.get("index", 0)))
    vecs = []
    for item in ordered:
        emb = item.get("embedding")
        if not isinstance(emb, list):
            raise EmbedError("返回的 embedding 格式无效。")
        vecs.append(emb)
    return vecs


def _parse_ollama(payload: Any) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise EmbedError("Ollama 接口返回无效。")
    if isinstance(payload.get("embeddings"), list) and payload["embeddings"]:
        return payload["embeddings"]
    if isinstance(payload.get("embedding"), list) and payload["embedding"]:
        return [payload["embedding"]]
    raise EmbedError("Ollama 接口未返回 embeddings。")


def _post_json(client: httpx.Client, url: str, body: dict, api_key: str) -> Any:
    try:
        resp = client.post(url, json=body, headers=_headers(api_key))
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        text = exc.response.text[:300]
        raise EmbedError(f"向量服务 HTTP {exc.response.status_code}：{text}") from exc
    except httpx.RequestError as exc:
        raise EmbedError(f"无法连接向量服务：{exc}") from exc


def embed_texts(
    texts: list[str],
    base_url: str,
    model: str,
    api_key: str = "",
    batch_size: int = 32,
    on_progress: ProgressFn | None = None,
    read_timeout: float = 180.0,
) -> np.ndarray:
    if not texts:
        raise EmbedError("没有可向量化的文本。")
    base = _normalize_base(base_url)
    if not base or not model.strip():
        raise EmbedError("未配置向量服务地址或模型名。")

    timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=30.0, pool=10.0)
    openai_url = _openai_url(base)
    ollama_url = _ollama_url(base)
    use_ollama = False
    vectors: list[list[float]] = []

    with httpx.Client(timeout=timeout) as client:
        # 先打 OpenAI 兼容（vLLM / Ollama /v1），不通再试 Ollama /api/embed；后面批次跟第一批
        first = texts[:batch_size]
        try:
            payload = _post_json(
                client,
                openai_url,
                {"model": model, "input": first},
                api_key,
            )
            vectors.extend(_parse_openai(payload))
        except EmbedError:
            payload = _post_json(
                client,
                ollama_url,
                {"model": model, "input": first},
                api_key,
            )
            vectors.extend(_parse_ollama(payload))
            use_ollama = True

        if on_progress:
            on_progress(len(vectors), len(texts), "正在调用向量服务")

        start = batch_size
        while start < len(texts):
            chunk = texts[start : start + batch_size]
            if use_ollama:
                payload = _post_json(
                    client, ollama_url, {"model": model, "input": chunk}, api_key
                )
                vectors.extend(_parse_ollama(payload))
            else:
                payload = _post_json(
                    client, openai_url, {"model": model, "input": chunk}, api_key
                )
                vectors.extend(_parse_openai(payload))
            start += len(chunk)
            if on_progress:
                on_progress(min(start, len(texts)), len(texts), "正在调用向量服务")

    if len(vectors) != len(texts):
        raise EmbedError(f"返回向量数 {len(vectors)} 与试题数 {len(texts)} 不一致。")
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2:
        raise EmbedError("向量维度不一致，无法组成矩阵。")
    return arr


def probe_embed(base_url: str, model: str, api_key: str = "") -> int:
    """发一条短文本探测服务是否可用，成功返回向量维度。"""
    vec = embed_texts(
        ["试题文义查重连通测试"],
        base_url,
        model,
        api_key,
        read_timeout=30.0,
    )
    if vec.shape[0] != 1 or vec.shape[1] < 8:
        raise EmbedError("向量服务返回的数据无效。")
    return int(vec.shape[1])
