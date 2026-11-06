"""
Minimal offline stand-in for the `genlayer` SDK. TEST-ONLY, not part of the
deployable contract.

The real SDK (and the GenVM runtime) exists only inside a GenLayer node or
GenLayer Studio. This stub reproduces just enough of its surface to import
and exercise the contract in plain Python:

  - storage types (TreeMap, DynArray, u256) with the real SDK's habit of
    starting empty, so a contract must not assign {} or [] to them;
  - gl.public decorators (no-ops), gl.message (settable caller and value);
  - gl.nondet.web.render / gl.nondet.exec_prompt, patched per test;
  - gl.vm.run_nondet_unsafe(leader_fn, validator_fn) with positional
    arguments, a leader result that goes through a JSON round trip (the
    real runtime moves it as calldata, so sets, tuples and floats are not
    allowed), and a validator that receives gl.vm.Return with the value on
    .calldata;
  - gl.eq_principle.strict_eq, gl.evm.contract_interface with a recorded
    emit_transfer ledger.

It deliberately does NOT simulate real network access, real model
behaviour, multi-validator consensus or GenVM's pinned clock. The test
bootstrap replaces the contract's single clock function with a controllable
one instead.
"""
import json

__all__ = ["gl", "TreeMap", "u256", "DynArray", "i256", "bigint", "Address"]


class _SubscriptableContainer:
    def __class_getitem__(cls, item):
        return cls


class TreeMap(_SubscriptableContainer, dict):
    """Persistent map stand-in: behaves like a dict."""


class DynArray(_SubscriptableContainer, list):
    """Persistent array stand-in: behaves like a list."""


class u256(int):
    """Unsigned 256-bit integer: negative or oversized values raise, as in
    the real SDK."""

    def __new__(cls, value=0):
        number = int(value)
        if number < 0 or number >= 2 ** 256:
            raise OverflowError("u256 out of range")
        return super().__new__(cls, number)


class i256(int):
    pass


class bigint(int):
    pass


class Address:
    """A 0x-prefixed 20-byte hex address, compared case-insensitively."""

    def __init__(self, value):
        text = str(value)
        if not text.startswith("0x") or len(text) != 42:
            raise ValueError(f"invalid address: {text!r}")
        int(text[2:], 16)
        self._value = text

    def __str__(self):
        return self._value

    def __repr__(self):
        return f"Address({self._value!r})"

    def __eq__(self, other):
        return str(self).lower() == str(other).lower()

    def __hash__(self):
        return hash(str(self).lower())


class UserError(Exception):
    """gl.vm.UserError"""


class _Vm:
    UserError = UserError

    class Return:
        def __init__(self, calldata):
            self.calldata = calldata

    @staticmethod
    def run_nondet_unsafe(leader_fn, validator_fn):
        result = leader_fn()
        # Calldata round trip: only JSON-like values survive the real runtime.
        result = json.loads(json.dumps(result, allow_nan=False))
        if not validator_fn(_Vm.Return(result)):
            raise UserError("validator rejected leader result")
        return result


class _WriteDecorator:
    def __call__(self, fn):
        return fn

    @staticmethod
    def payable(fn):
        return fn


class _PublicNamespace:
    write = _WriteDecorator()

    @staticmethod
    def view(fn):
        return fn


class _NondetWeb:
    @staticmethod
    def render(url, mode="text"):
        raise NotImplementedError("gl.nondet.web.render must be patched in tests")


class _Nondet:
    web = _NondetWeb()

    @staticmethod
    def exec_prompt(prompt, response_format="text"):
        raise NotImplementedError("gl.nondet.exec_prompt must be patched in tests")


class _EqPrinciple:
    @staticmethod
    def strict_eq(fn):
        return fn()


class _Message:
    sender_address = None
    value = u256(0)


class _Contract:
    """gl.Contract stand-in. Storage-typed fields declared as class-level
    annotations start out empty, exactly like the real SDK."""

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        for klass in reversed(cls.__mro__):
            for name, annotation in vars(klass).get("__annotations__", {}).items():
                if isinstance(annotation, type) and issubclass(annotation, (TreeMap, DynArray)):
                    setattr(instance, name, annotation())
                elif annotation is u256:
                    setattr(instance, name, u256(0))
        instance.balance = u256(0)
        return instance

    def __init__(self, *args, **kwargs):
        pass


class _EvmContractProxy:
    def __init__(self, address):
        self.address = address

    def emit_transfer(self, value):
        _EvmNamespace.transfers.append({"to": str(self.address), "value": u256(value)})


class _EvmNamespace:
    transfers = []

    @staticmethod
    def contract_interface(cls):
        return _EvmContractProxy


class _GL:
    Contract = _Contract
    public = _PublicNamespace()
    nondet = _Nondet()
    eq_principle = _EqPrinciple()
    vm = _Vm()
    message = _Message()
    evm = _EvmNamespace()


gl = _GL()
