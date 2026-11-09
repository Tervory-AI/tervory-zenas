"""rig command declarations; argument classes remain exported by command."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SearchArgs(Args):
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=12, ge=1, le=24)


class ProductArgs(Args):
    product_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.:-]+$')


class CompareArgs(Args):
    product_ids: list[str] = Field(min_length=1, max_length=24)


class SwapArgs(Args):
    setup: dict
    role: str = Field(min_length=1, max_length=80)


class QuoteArgs(Args):
    product_id: str | None = Field(default=None, max_length=80)
    setup: dict | None = None
    title: str | None = Field(default=None, max_length=200)
    request: str | None = Field(default=None, max_length=2000)
    quantity: int | None = Field(default=None, ge=1, le=100000)
    contact: dict
    notice_version: str | None = Field(default=None, max_length=64)


class IdentityArgs(Args):
    id: UUID


class ReviewArgs(Args):
    scale: Literal[True]
    appearance: Literal[True]
    attachment_points: Literal[True]


class ApprovalArgs(IdentityArgs):
    review: ReviewArgs


class ForgeArgs(ProductArgs):
    id: UUID
    source: Literal['ai', 'scan']
    inputs: list[str] = Field(min_length=1, max_length=70)
    dimensions_mm: tuple[float, float, float]
    anchors: dict
    cost_cap_usd: float = Field(ge=0, le=100)
    timeout_seconds: int = Field(default=900, ge=60, le=3600)


class CartSession(Args):
    session: str = Field(pattern=r'^[A-Za-z0-9_-]{43,100}$')


class CartView(CartSession):
    product_id: str | None = Field(default=None, max_length=80)
    recheck: bool = False


class CartAdd(CartSession):
    product_id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=100000, strict=True)
    back_order: bool = False
    line_id: str | None = Field(default=None, max_length=128)


class CartRemove(CartSession):
    line_id: str = Field(min_length=1, max_length=128)


class CartOrder(CartSession):
    checkout_id: str = Field(min_length=1, max_length=128)


MAIN = ['rig.search', 'rig.ask', 'rig.product', 'rig.setups', 'rig.graph', 'rig.compare', 'rig.swap',
        'rig.quote', 'rig.receipts', 'rig.receipt', 'rig.suggest', 'rig.forge.submit', 'rig.forge.poll', 'rig.forge.approve', *['rig.cart.' + action for action in ('session','view','add','remove','checkout','order')]]
ADMIN = ['rig.receipts', 'rig.receipt', 'rig.suggest', 'rig.forge.submit', 'rig.forge.poll', 'rig.forge.approve']
TOOLS = {'rig': ['rig.search', 'rig.ask', 'rig.quote.confirm_payment', 'rig.store.onboard', 'rig.rooms.list', 'rig.store.destroy', 'rig.product', 'rig.setups', 'rig.graph', 'rig.compare', 'rig.swap', 'rig.quote', 'rig.receipts', 'rig.receipt', 'rig.suggest', 'rig.forge.submit', 'rig.forge.poll', 'rig.forge.approve', 'rig.cart.session', 'rig.cart.view', 'rig.cart.add', 'rig.cart.remove', 'rig.cart.checkout', 'rig.cart.order']}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = ['rig.quote.confirm_payment', *ADMIN]
ADMIN_ONLY_TOOLS = frozenset(['rig.quote.confirm_payment', 'rig.store.onboard', 'rig.rooms.list', 'rig.store.destroy', *ADMIN])
REQUEST_BODY_TOOLS = frozenset(MAIN)
CONFIRMED = frozenset(['rig.quote.confirm_payment', 'rig.store.onboard', 'rig.store.destroy', *MAIN])

def adapters(command):
    return {
        'rig.search': command.Adapter('GET', '/v1/rig/tools/search', SearchArgs, 'Search the product catalogue'),
        'rig.ask': command.Adapter('POST', '/v1/rig/tools/ask', command.RigAskArgs, 'Ask about a product', True, 'paid_run'),
        'rig.quote.confirm_payment': command.Adapter('POST', '/v1/access/rig/admin/confirm-store-payment', command.RigConfirmPaymentArgs, 'rig.quote.confirm_payment'),
        'rig.store.onboard': command.Adapter('POST', '/v1/access/rig/rooms/onboard', command.EmptyArgs, 'rig.store.onboard'),
        'rig.rooms.list': command.Adapter('POST', '/v1/access/rig/rooms/list', command.EmptyArgs, 'rig.rooms.list'),
        'rig.store.destroy': command.Adapter('POST', '/v1/access/rig/rooms/destroy', command.EmptyArgs, 'rig.store.destroy'),
        'rig.product': command.Adapter('POST', '/v1/rig/tools/product', ProductArgs, 'Product'),
        'rig.setups': command.Adapter('POST', '/v1/rig/tools/setups', ProductArgs, 'Explore this setup'),
        'rig.graph': command.Adapter('POST', '/v1/rig/tools/graph', ProductArgs, 'Product connections'),
        'rig.compare': command.Adapter('POST', '/v1/rig/tools/compare', CompareArgs, 'Compare'),
        'rig.swap': command.Adapter('POST', '/v1/rig/tools/swap', SwapArgs, 'Swap'),
        'rig.quote': command.Adapter('POST', '/v1/rig/tools/quote', QuoteArgs, 'Request a quote'),
        'rig.receipts': command.Adapter('GET', '/v1/rig/tools/receipts', command.EmptyArgs, 'Receipts'),
        'rig.receipt': command.Adapter('POST', '/v1/rig/tools/receipt', IdentityArgs, 'Receipt'),
        'rig.suggest': command.Adapter('POST', '/v1/rig/tools/suggest', command.EmptyArgs, 'Suggest fits'),
        'rig.forge.submit': command.Adapter('POST', '/v1/rig/tools/forge.submit', ForgeArgs, 'Prepare product', True, 'paid_run'),
        'rig.forge.poll': command.Adapter('POST', '/v1/rig/tools/forge.poll', IdentityArgs, '3D product view'),
        'rig.forge.approve': command.Adapter('POST', '/v1/rig/tools/forge.approve', ApprovalArgs, 'Approve'),
        **{'rig.cart.' + operation: command.Adapter('POST', '/v1/rig/tools/cart.' + operation,
            schema, label) for operation, schema, label in [
                ('session', command.EmptyArgs, 'Cart'), ('view', CartView, 'Cart'),
                ('add', CartAdd, 'Add'), ('remove', CartRemove, 'Remove'),
                ('checkout', CartSession, 'Checkout'), ('order', CartOrder, 'Order')]},
    }

TOOLS["rig"].extend(["rig.build", "rig.memory.view", "rig.memory.edit", "rig.memory.forget", "rig.memory.switch"])


class BuildArgs(Args):
    task_id: UUID
    home_region: Literal['us-east'] = 'us-east'
    question: str = Field(min_length=1, max_length=8000)


class MemoryEditArgs(Args):
    memory_id: UUID | None = None
    text: str = Field(min_length=1, max_length=8000)
    kind: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=200)
    task_key: str | None = Field(default=None, max_length=200)


class MemoryForgetArgs(Args):
    memory_id: UUID


class MemorySwitchArgs(Args):
    enabled: bool


_base_adapters = adapters


def adapters(command):
    return {**_base_adapters(command),
        'rig.build': command.Adapter('POST', '/v1/rig/tasks', BuildArgs, 'Build'),
        'rig.memory.view': command.Adapter('GET', '/v1/rig/memory/records', command.EmptyArgs, 'Memory'),
        'rig.memory.edit': command.Adapter('POST', '/v1/rig/memory/edit', MemoryEditArgs, 'Edit'),
        'rig.memory.forget': command.Adapter('POST', '/v1/rig/memory/forget', MemoryForgetArgs, 'Forget'),
        'rig.memory.switch': command.Adapter('POST', '/v1/rig/memory/switch', MemorySwitchArgs, 'Memory')}


REQUEST_BODY_TOOLS = REQUEST_BODY_TOOLS | {'rig.build', 'rig.memory.edit', 'rig.memory.forget', 'rig.memory.switch'}
CONFIRMED = CONFIRMED | {'rig.build', 'rig.memory.edit', 'rig.memory.forget', 'rig.memory.switch'}
