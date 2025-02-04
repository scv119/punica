import torch
import punica.ops._kernels

__all__ = [
    "rotary_mha_decode",
    "bgmv",
    "add_lora",
]


def rotary_mha_decode(
    q_proj: torch.Tensor,
    k_proj: torch.Tensor,
    v_proj: torch.Tensor,
    past_lens: torch.LongTensor,
    kvbuf: torch.Tensor,
    kvidx: torch.LongTensor,
    layer_idx: int,
) -> torch.Tensor:
  """
  Semantics of `rotary_mha_decode`:
  For each input in the batch:
  1. Apply rotary position encoding to `q_proj` and `k_proj`.
  2. Write `v_proj` and the position encoded `k_proj` to the KV cache.
  3. Perform multi-head attention.

  All inputs in the batch should be in the decoding (auto-regression) stage,
  i.e., each input should only have one token.
  Inputs can have different `past_lens` in the KV cache.
  The KV cache of the `i`-th input locates at `kvbuf[kvidx[i], ...]`.
  `layer_idx` specifies the layer index of the KV cache.

  The implementation uses Flash Attention [1] to save memory and improve
  performance.

  Notations for shapes:
  `B`: batch size
  `N`: number of heads
  `D`: head dimension
  `L`: number of layers
  `MAXLEN`: maximum length of the KV cache

  Args:
    q_proj: Shape: `[B, N, D]`. Query projection (`X @ W_q`).
    k_proj: Shape: `[B, N, D]`. Key projection (`X @ W_k`).
    v_proj: Shape: `[B, N, D]`. Value projection (`X @ W_v`).
    past_lens: Shape: `[B]`. Lengths of the past KV cache.
    kvbuf: Shape: `[None, L, 2, MAXLEN, N, D]`. KV cache buffer.\
        Note that `kvbuf` will be changed in-place.
    kvidx: Shape: `[B]`. Indices of the KV cache in `kvbuf`.
    layer_idx: Layer index of the KV cache.

  Returns:
    Shape: `[B, N, D]`. Output of the multi-head attention.

  References:
    [1] Dao, Tri, Dan Fu, Stefano Ermon, Atri Rudra, and Christopher Ré.
        "Flashattention: Fast and memory-efficient exact attention with io-awareness."
        Advances in Neural Information Processing Systems 35 (2022): 16344-16359.
        https://arxiv.org/abs/2205.14135
  """
  f = punica.ops._kernels.dispatch_rotary_mha_decode
  device = q_proj.device
  dtype = q_proj.dtype
  if dtype != torch.float16:
    raise ValueError(f"Unsupported dtype: {dtype}")

  o = torch.empty(q_proj.shape, dtype=dtype, device=device)
  f(q_proj, k_proj, v_proj, o, past_lens, kvbuf, kvidx, layer_idx)
  return o


def bgmv(
    y: torch.Tensor,
    x: torch.Tensor,
    w_T_all: torch.Tensor,
    indicies: torch.LongTensor,
    layer_idx: int,
    scale: float,
):
  """
  Semantics:
    y[i] += (
        x[i].unsqueeze(0)
        @ w_T_all[indices[i], layer_idx, :, :].transpose(-1, -2)
        * scale
      ).squeeze(0)

  Args:
    y: Shape: `[B, H2]`. Output vectors. Will be changed in-place.
    x: Shape: `[B, H1]`. Input vectors.
    w_T_all: Shape: `[None, L, H2, H1]`. All of the transposed weight matrices.
    indicies: Shape: `[B]`. Indices of the weight matrices.
    layer_idx: Layer index of the weight matrices.
    scale: Scaling factor.
  """
  f = punica.ops._kernels.dispatch_bgmv
  f(y, x, w_T_all, indicies, layer_idx, scale)


def add_lora(
    y: torch.Tensor,
    x: torch.Tensor,
    wa_T_all: torch.Tensor,
    wb_T_all: torch.Tensor,
    indicies: torch.LongTensor,
    layer_idx: int,
    scale: float,
):
  """
  Semantics:
    y[i] += (
        x[i].unsqueeze(0)
        @ wa_T_all[indices[i], layer_idx, :, :].transpose(-1, -2)
        @ wb_T_all[indices[i], layer_idx, :, :].transpose(-1, -2)
        * scale
      ).squeeze(0)

  Args:
    y: Shape: `[B, H2]`. Output vectors. Will be changed in-place.
    x: Shape: `[B, H1]`. Input vectors.
    wa_T_all: Shape: `[None, L, R, H1]`. All of the transposed LoRA A matrices.
    wb_T_all: Shape: `[None, L, H2, R]`. All of the transposed LoRA B matrices.
    indicies: Shape: `[B]`. Indices of the LoRA weights.
    layer_idx: Layer index of LoRA weights.
    scale: Scaling factor.
  """
  f = punica.ops._kernels.dispatch_bgmv
  device = x.device
  dtype = x.dtype

  r = wb_T_all.size(-1)
  tmp = torch.zeros((x.size(0), r), dtype=dtype, device=device)
  f(tmp, x, wa_T_all, indicies, layer_idx, 1.0)
  f(y, tmp, wb_T_all, indicies, layer_idx, scale)
