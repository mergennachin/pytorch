import functools
import logging

import sympy

import torch
from torch._inductor import config as inductor_config
from torch._inductor.ir import FixedLayout
from torch._inductor.select_algorithm import realize_inputs
from torch._inductor.virtualized import V
from ..utils import cdiv


log = logging.getLogger(__name__)


@functools.lru_cache(None)
def mm_configs():
    import triton

    return [
        triton.Config(
            {"BLOCK_M": 64, "BLOCK_N": 64, "BLOCK_K": 32}, num_stages=2, num_warps=4
        ),
        triton.Config(
            {"BLOCK_M": 64, "BLOCK_N": 128, "BLOCK_K": 32}, num_stages=3, num_warps=4
        ),
        triton.Config(
            {"BLOCK_M": 128, "BLOCK_N": 64, "BLOCK_K": 32}, num_stages=3, num_warps=4
        ),
        triton.Config(
            {"BLOCK_M": 64, "BLOCK_N": 128, "BLOCK_K": 32}, num_stages=4, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 128, "BLOCK_N": 64, "BLOCK_K": 32}, num_stages=4, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 64, "BLOCK_N": 32, "BLOCK_K": 32}, num_stages=5, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 32, "BLOCK_N": 64, "BLOCK_K": 32}, num_stages=5, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 128, "BLOCK_N": 128, "BLOCK_K": 32}, num_stages=2, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 64, "BLOCK_N": 64, "BLOCK_K": 64}, num_stages=3, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 32, "BLOCK_N": 32, "BLOCK_K": 128}, num_stages=2, num_warps=8
        ),
        triton.Config(
            {"BLOCK_M": 64, "BLOCK_N": 64, "BLOCK_K": 16}, num_stages=2, num_warps=4
        ),
        triton.Config(
            {"BLOCK_M": 32, "BLOCK_N": 32, "BLOCK_K": 16}, num_stages=1, num_warps=2
        ),
    ]


def mm_grid(m, n, meta):
    """
    The CUDA grid size for matmul triton templates.
    """
    return (cdiv(m, meta["BLOCK_M"]) * cdiv(n, meta["BLOCK_N"]), 1, 1)


def acc_type(dtype):
    if dtype in (torch.float16, torch.bfloat16):
        return "tl.float32"
    return f"tl.{dtype}".replace("torch.", "")


@functools.lru_cache(None)
def is_big_gpu(index):
    cores = torch.cuda.get_device_properties(index).multi_processor_count
    if cores < 80:  # V100
        log.warning("not enough cuda cores to use max_autotune mode")
        return False
    return True


def use_triton_template(layout):
    return (
        inductor_config.max_autotune
        and layout.device.type == "cuda"
        and layout.dtype in (torch.float16, torch.bfloat16, torch.float32)
        and is_big_gpu(layout.device.index or 0)
    )


def mm_options(config, sym_k, layout):
    """
    Common options to matmul triton templates.
    """
    even_k_symbolic = (
        # it isn't worth guarding on this
        sympy.gcd(sym_k, config.kwargs["BLOCK_K"])
        == config.kwargs["BLOCK_K"]
    )
    return dict(
        GROUP_M=8,
        EVEN_K=even_k_symbolic,
        ALLOW_TF32=torch.backends.cuda.matmul.allow_tf32,
        ACC_TYPE=acc_type(layout.dtype),
        num_stages=config.num_stages,
        num_warps=config.num_warps,
        **config.kwargs,
    )


def mm_args(mat1, mat2, *others, layout=None):
    """
    Common arg processing for mm,bmm,addmm,etc
    """
    mat1, mat2 = realize_inputs(mat1, mat2)
    *b1, m, k1 = mat1.get_size()
    *b2, k2, n = mat2.get_size()
    b = [V.graph.sizevars.guard_equals(a, b) for a, b in zip(b1, b2)]
    k = V.graph.sizevars.guard_equals(k1, k2)
    if layout is None:
        layout = FixedLayout(
            mat1.get_device(),
            mat1.get_dtype(),
            [*b, m, n],
        )

    from ..lowering import expand

    others = [realize_inputs(expand(x, layout.size)) for x in others]

    return [m, n, k, layout, mat1, mat2, *others]


def addmm_epilogue(dtype, alpha, beta):
    def epilogue(acc, bias):
        if alpha != 1:
            acc = V.ops.mul(acc, V.ops.constant(alpha, dtype))
        if beta != 1:
            bias = V.ops.mul(bias, V.ops.constant(beta, dtype))
        return V.ops.add(acc, bias)

    return epilogue
