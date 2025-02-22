import types
from typing import Tuple
from typing import Union

import typeguard
from typing_extensions import get_args

from phantom.utils import is_union_type

from .base import Predicate
from .utils import bind_name


def equal(a: object) -> Predicate[object]:
    """Create a new predicate that succeeds when its argument is equal to ``a``."""

    @bind_name(equal, a)
    def check(b: object) -> bool:
        return a == b

    return check


def identical(a: object) -> Predicate[object]:
    """Create a new predicate that succeeds when its argument is identical to ``a``."""

    @bind_name(identical, a)
    def check(b: object) -> bool:
        return a is b

    return check


def of_type(t: Union[type, Tuple[type, ...]]) -> Predicate[object]:
    """
    Create a new predicate that succeeds when its argument is an instance of ``t``.
    """

    @bind_name(of_type, t)
    def check(a: object) -> bool:
        return isinstance(a, t)

    return check


def of_complex_type(t: type) -> Predicate[object]:
    # Hack to support PEP 604 before it's implemented in typeguard, tracking issue for
    # that feature is here: https://github.com/agronholm/typeguard/issues/222
    # Rewrite types.UnionType objects generated by PEP 604 unions as types.GenericAlias,
    # as if it was generated by a Union[].
    t = (
        types.GenericAlias(Union, get_args(t))  # type: ignore
        if is_union_type(t)
        else t
    )

    @bind_name(of_complex_type, t)
    def check(a: object) -> bool:
        try:
            typeguard.check_type("a", a, t, globals={}, locals={})
        except TypeError:
            return False
        return True

    return check
