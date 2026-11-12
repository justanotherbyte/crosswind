from typing import Any

from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ("AI",)


class AI(Binding):
    async def run(
        self,
        model: str,
        inputs: dict[str, Any],
        options: Any = None,
        /,
        *,
        gateway: dict[str, Any] | None = None,
        return_raw_response: bool | None = None,
        extra_headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> Any:
        js_options = _js_options(
            options,
            extra,
            gateway=gateway,
            return_raw_response=return_raw_response,
            extra_headers=extra_headers,
        )
        result = await self.js_object.run(model, python_to_rpc(inputs), js_options)
        # A stream stays a js.ReadableStream and a raw response becomes a Response.
        return python_from_rpc(result)

    async def models(self, params: Any = None, /, **extra: Any) -> list[dict[str, Any]]:
        return python_from_rpc(await self.js_object.models(_js_options(params, extra)))

    def gateway(self, gateway_id: str) -> Binding:
        return Binding(self.js_object.gateway(gateway_id))
