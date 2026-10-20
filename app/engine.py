"""
engine.py — Kronos inference wrapper for Kronos Lab.

Loads Kronos once and forecasts many series at once, keeping every sampled
path (the upstream predictor averages them away). Each sampled path gives us
a forecast range, not one falsely precise line.

Determinism: every call seeds torch with a fixed seed, so the same candles in
always give the same forecast out. The model is a frozen, pre-trained network.
It is not a language model and it does not reason about trades.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch

from .kronos_model import Kronos, KronosTokenizer
from .kronos_model.kronos import sample_from_logits

log = logging.getLogger("kronos.engine")

# ── Config ───────────────────────────────────────────────────────────────
MODEL_ID = "NeoQuasar/Kronos-small"
TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
MAX_CONTEXT = 512          # hard limit for Kronos-small
DEFAULT_LOOKBACK = 256     # daily bars fed to the model (~1 trading year)
CLIP = 5.0                 # z-score clip, same as upstream
SEED = 1234
TOP_P = 0.9
TEMPERATURE = 1.0
BATCH_SEQS = 40            # max (series x samples) per forward batch, keeps RAM sane
TRAINING_DATA_END = "2024-06-30"   # per the Kronos paper: pre-training data ends June 2024

PRICE_COLS = ["open", "high", "low", "close"]
FEAT_COLS = PRICE_COLS + ["volume", "amount"]


@dataclass
class ForecastResult:
    """Forecast for one series. `paths` shape: (samples, horizon, 6 features)."""
    future_index: pd.DatetimeIndex
    paths: np.ndarray

    def close_paths(self) -> np.ndarray:
        return self.paths[:, :, 3]

    def summary(self, last_close: float) -> dict:
        closes = self.close_paths()
        final = closes[:, -1]
        rets = final / last_close - 1.0
        return {
            "expected_return": float(np.mean(rets)),
            "median_return": float(np.median(rets)),
            "p10_return": float(np.percentile(rets, 10)),
            "p90_return": float(np.percentile(rets, 90)),
            "prob_up": float(np.mean(rets > 0)),
            "dispersion": float(np.std(rets)),
        }


def _time_features(idx: pd.DatetimeIndex) -> np.ndarray:
    # Same 5 calendar features Kronos was trained with.
    return np.stack([idx.minute, idx.hour, idx.weekday, idx.day, idx.month], axis=1).astype(np.float32)


def future_business_days(last: pd.Timestamp, n: int) -> pd.DatetimeIndex:
    """Next n weekdays after `last`. NSE holidays are ignored; they only affect calendar features."""
    return pd.bdate_range(last + pd.Timedelta(days=1), periods=n)


class KronosEngine:
    def __init__(self, model_id: str = MODEL_ID, tokenizer_id: str = TOKENIZER_ID, threads: int | None = None):
        self.model_id = model_id
        self.tokenizer_id = tokenizer_id
        self._lock = threading.Lock()       # one inference at a time; CPU is the bottleneck anyway
        self._model = None
        self._tok = None
        if threads:
            torch.set_num_threads(threads)

    # Lazy load so the web server can boot (and serve the UI) before weights download.
    def load(self) -> None:
        if self._model is not None:
            return
        t0 = time.time()
        try:
            self._tok = KronosTokenizer.from_pretrained(self.tokenizer_id).eval()
            self._model = Kronos.from_pretrained(self.model_id).eval()
        except Exception as e:  # network / hub problems
            log.exception("Failed to load Kronos weights")
            raise RuntimeError(f"Could not load Kronos weights from Hugging Face: {e}") from e
        log.info("Kronos loaded in %.1fs", time.time() - t0)

    @property
    def ready(self) -> bool:
        return self._model is not None

    def forecast(self, frames: list[pd.DataFrame], horizon: int, samples: int = 8,
                 lookback: int = DEFAULT_LOOKBACK) -> list[ForecastResult]:
        """
        frames: DataFrames indexed by date with columns open/high/low/close/volume.
        All frames are cut to the same lookback (shorter ones are rejected upstream).
        """
        if not frames:
            return []
        self.load()
        lookback = min(lookback, MAX_CONTEXT - horizon)
        xs, x_stamps, y_stamps, means, stds, fut_idx = [], [], [], [], [], []
        for df in frames:
            d = df.tail(lookback).copy()
            if len(d) < lookback:
                raise ValueError(f"series has {len(d)} bars, need {lookback}")
            if "volume" not in d:
                d["volume"] = 0.0
            d["volume"] = d["volume"].fillna(0.0)
            d["amount"] = d["volume"] * d[PRICE_COLS].mean(axis=1)
            x = d[FEAT_COLS].to_numpy(np.float32)
            if not np.isfinite(x).all():
                raise ValueError("series contains NaN/inf")
            m, s = x.mean(axis=0), x.std(axis=0)
            xs.append(np.clip((x - m) / (s + 1e-5), -CLIP, CLIP))
            means.append(m)
            stds.append(s)
            fi = future_business_days(pd.Timestamp(d.index[-1]), horizon)
            fut_idx.append(fi)
            x_stamps.append(_time_features(pd.DatetimeIndex(d.index)))
            y_stamps.append(_time_features(fi))

        x = np.stack(xs)
        xs_t = np.stack(x_stamps)
        ys_t = np.stack(y_stamps)

        # Chunk series so (series x samples) per pass stays under BATCH_SEQS.
        per_chunk = max(1, BATCH_SEQS // samples)
        out = []
        with self._lock:
            torch.manual_seed(SEED)
            for i in range(0, len(frames), per_chunk):
                sl = slice(i, i + per_chunk)
                out.append(self._infer_cached(x[sl], xs_t[sl], ys_t[sl], horizon, samples))
        paths = np.concatenate(out, axis=0)  # (B, S, H, F)

        results = []
        for b in range(len(frames)):
            p = paths[b] * (stds[b] + 1e-5) + means[b]
            results.append(ForecastResult(future_index=fut_idx[b], paths=p))
        return results

    def _infer_cached(self, x, x_stamp, y_stamp, pred_len, sample_count) -> np.ndarray:
        """
        Same sampling procedure as upstream, but each new candle reuses cached attention
        keys/values instead of re-running the whole context: ~10-30x faster on CPU.
        Valid while context + horizon fits in MAX_CONTEXT (no sliding window), which
        forecast() guarantees by capping the lookback.
        """
        tok, model = self._tok, self._model
        with torch.no_grad():
            x = torch.from_numpy(x)
            x_stamp = torch.from_numpy(x_stamp)
            y_stamp = torch.from_numpy(y_stamp)
            B = x.size(0)
            rep = lambda t: t.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, t.size(1), t.size(2))
            x, x_stamp, y_stamp = rep(x), rep(x_stamp), rep(y_stamp)
            L0 = x.size(1)
            if L0 + pred_len > MAX_CONTEXT:
                raise ValueError("lookback + horizon exceeds the model context")

            pre, post = tok.encode(x, half=True)
            caches = [None] * len(model.transformer)
            h = model.embedding([pre, post]) + model.time_emb(x_stamp)
            ctx = model.norm(_run_blocks(model, h, caches, 0))
            new_pre, new_post = [], []
            for i in range(pred_len):
                s_pre = _sample(model.head(ctx[:, -1, :]))
                s_post = _sample(model.decode_s2(ctx, s_pre)[:, -1, :])
                new_pre.append(s_pre)
                new_post.append(s_post)
                if i < pred_len - 1:
                    h = model.embedding([s_pre, s_post]) + model.time_emb(y_stamp[:, i:i + 1, :])
                    ctx = torch.cat([ctx, model.norm(_run_blocks(model, h, caches, L0 + i))], dim=1)

            full_pre = torch.cat([pre] + new_pre, dim=1)
            full_post = torch.cat([post] + new_post, dim=1)
            z = tok.decode([full_pre, full_post], half=True)
            z = z.reshape(B, sample_count, z.size(1), z.size(2)).cpu().numpy()
            return z[:, :, -pred_len:, :]

    def _infer(self, x, x_stamp, y_stamp, pred_len, sample_count) -> np.ndarray:
        """Upstream auto_regressive_inference, minus the final averaging over samples."""
        tok, model = self._tok, self._model
        with torch.no_grad():
            x = torch.from_numpy(x)
            x_stamp = torch.from_numpy(x_stamp)
            y_stamp = torch.from_numpy(y_stamp)
            B = x.size(0)
            x = x.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, x.size(1), x.size(2))
            x_stamp = x_stamp.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, x_stamp.size(1), x_stamp.size(2))
            y_stamp = y_stamp.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, y_stamp.size(1), y_stamp.size(2))

            x_token = tok.encode(x, half=True)
            init_len = x.size(1)
            total_len = init_len + pred_len
            full_stamp = torch.cat([x_stamp, y_stamp], dim=1)
            pre, post = x_token
            for i in range(pred_len):
                cur = init_len + i
                start = max(0, cur - MAX_CONTEXT)
                s1_logits, ctx = model.decode_s1(pre[:, start:cur], post[:, start:cur],
                                                 full_stamp[:, start:cur, :].contiguous())
                s_pre = _sample(s1_logits[:, -1, :])
                s2_logits = model.decode_s2(ctx, s_pre)
                s_post = _sample(s2_logits[:, -1, :])
                pre = torch.cat([pre, s_pre], dim=1)
                post = torch.cat([post, s_post], dim=1)

            start = max(0, total_len - MAX_CONTEXT)
            z = tok.decode([pre[:, start:total_len].contiguous(), post[:, start:total_len].contiguous()], half=True)
            z = z.reshape(B, sample_count, z.size(1), z.size(2)).cpu().numpy()
            return z[:, :, -pred_len:, :]


def _rope(inv_freq: torch.Tensor, pos0: int, length: int):
    t = torch.arange(pos0, pos0 + length, device=inv_freq.device).type_as(inv_freq)
    emb = torch.cat([torch.outer(t, inv_freq)] * 2, dim=-1)
    return emb.cos()[None, None], emb.sin()[None, None]


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat((-x2, x1), dim=-1)


def _run_blocks(model, h: torch.Tensor, caches: list, pos0: int) -> torch.Tensor:
    """Kronos transformer stack with a key/value cache. Same maths as TransformerBlock.forward."""
    for li, blk in enumerate(model.transformer):
        a = blk.self_attn
        B, T, D = h.shape
        xin = blk.norm1(h)
        q = a.q_proj(xin).view(B, T, a.n_heads, a.head_dim).transpose(1, 2)
        k = a.k_proj(xin).view(B, T, a.n_heads, a.head_dim).transpose(1, 2)
        v = a.v_proj(xin).view(B, T, a.n_heads, a.head_dim).transpose(1, 2)
        cos, sin = _rope(a.rotary.inv_freq, pos0, T)
        q = q * cos + _rotate_half(q) * sin
        k = k * cos + _rotate_half(k) * sin
        first = caches[li] is None
        if not first:
            k = torch.cat([caches[li][0], k], dim=2)
            v = torch.cat([caches[li][1], v], dim=2)
        caches[li] = (k, v)
        att = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=first and T > 1)
        h = h + a.out_proj(att.transpose(1, 2).contiguous().view(B, T, D))
        h = h + blk.ffn(blk.norm2(h))
    return h


def _sample(logits: torch.Tensor) -> torch.Tensor:
    """Upstream sampler: temperature + nucleus (top-p) sampling."""
    return sample_from_logits(logits, temperature=TEMPERATURE, top_k=0, top_p=TOP_P, sample_logits=True)
