import torch
import torch.ao.nn.quantized as nnq
import torch.ao.nn.intrinsic as nni

__all__ = [
    "LinearReLU",

]

class LinearReLU(nnq.Linear):
    r"""
    A LinearReLU module fused from Linear and ReLU modules

    We adopt the same interface as :class:`torch.ao.nn.quantized.Linear`.

    Attributes:
        Same as torch.ao.nn.quantized.Linear

    Examples::

        >>> # xdoctest: +SKIP
        >>> m = nn.intrinsic.LinearReLU(20, 30)
        >>> input = torch.randn(128, 20)
        >>> output = m(input)
        >>> print(output.size())
        torch.Size([128, 30])
    """
    _FLOAT_MODULE = nni.LinearReLU

    def __init__(self, in_features, out_features, bias=True, dtype=torch.qint8):
        super().__init__(in_features, out_features, bias, dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.ops.quantized.linear_relu(
            x, self._packed_params._packed_params, self.scale, self.zero_point)

    def _get_name(self):
        return 'QuantizedLinearReLU'

    @classmethod
    def from_float(cls, mod):
        return super(LinearReLU, cls).from_float(mod)

    @classmethod
    def from_reference(cls, ref_linear_relu, output_scale, output_zero_point):
        return super().from_reference(ref_linear_relu[0], output_scale, output_zero_point)
