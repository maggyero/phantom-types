from __future__ import annotations

import abc
from typing import Any
from typing import Callable
from typing import ClassVar
from typing import Generic
from typing import Iterable
from typing import Iterator
from typing import Sequence
from typing import TypeVar
from typing import cast

from typing_extensions import Protocol
from typing_extensions import get_args
from typing_extensions import runtime_checkable

from .predicates.base import Predicate
from .predicates.boolean import all_of
from .predicates.generic import of_complex_type
from .predicates.generic import of_type
from .schema import SchemaField
from .utils import BoundType
from .utils import NotKnownMutableType
from .utils import UnresolvedClassAttribute
from .utils import fully_qualified_name
from .utils import is_not_mutable_type
from .utils import is_subtype
from .utils import is_union
from .utils import resolve_class_attr


@runtime_checkable
class InstanceCheckable(Protocol):
    def __instancecheck__(self, instance: object) -> bool:
        ...


class PhantomMeta(abc.ABCMeta):
    """
    Metaclass that defers __instancecheck__ to derived classes and prevents actual
    instance creation.
    """

    def __instancecheck__(self, instance: object) -> bool:
        if not issubclass(self, InstanceCheckable):
            return False
        return self.__instancecheck__(instance)

    # With the current level of metaclass support in mypy it's unlikely that we'll be
    # able to make this context typed, hence the ignores.
    def __call__(cls, instance):  # type: ignore[no-untyped-def]
        return cls.parse(instance)  # type: ignore[attr-defined]


class BoundError(TypeError):
    ...


T = TypeVar("T", covariant=True)


def display_bound(bound: Any) -> str:
    if isinstance(bound, Iterable):
        return f"Intersection[{', '.join(display_bound(part) for part in bound)}]"
    if is_union(bound):
        return (
            f"typing.Union["
            f"{', '.join(display_bound(part) for part in get_args(bound))}"
            f"]"
        )
    return str(getattr(bound, "__name__", bound))


def get_bound_parser(bound: Any) -> Callable[[object], T]:
    within_bound = (
        # Interpret sequence as intersection
        all_of(of_type(t) for t in bound)
        if isinstance(bound, Sequence)
        else of_complex_type(bound)
    )

    def parser(instance: object) -> T:
        if not within_bound(instance):
            raise BoundError(
                f"Value is not within bound of {display_bound(bound)!r}: {instance!r}"
            )
        return cast(T, instance)

    return parser


Derived = TypeVar("Derived", bound="PhantomBase")


class PhantomBase(SchemaField, metaclass=PhantomMeta):
    @classmethod
    def parse(cls: type[Derived], instance: object) -> Derived:
        """
        Parse an arbitrary value into a phantom type.

        :raises TypeError:
        """
        if not isinstance(instance, cls):
            raise TypeError(
                f"Could not parse {fully_qualified_name(cls)} from {instance!r}"
            )
        return instance

    @classmethod
    @abc.abstractmethod
    def __instancecheck__(cls, instance: object) -> bool:
        ...

    @classmethod
    def __get_validators__(cls: type[Derived]) -> Iterator[Callable[[object], Derived]]:
        """Hook that makes phantom types compatible with pydantic."""
        yield cls.parse


class AbstractInstanceCheck(TypeError):
    ...


class Phantom(PhantomBase, Generic[T]):
    """
    Base class for predicate-based phantom types.

    **Class arguments**

    * ``predicate: Predicate[T] | None`` - Predicate function used for instance checks.
      Can be ``None`` if the type is abstract.
    * ``bound: type[T] | None`` - Bound used to check values before passing them to the
      type's predicate function. This will often but not always be the same as the
      runtime type that values of the phantom type are represented as. If this is not
      provided as a class argument, it's attempted to be resolved in order from an
      implicit bound (any bases of the type that come before ``Phantom``), or inherited
      from super phantom types that provide a bound. Can be ``None`` if the type is
      abstract.
    * ``abstract: bool`` - Set to ``True`` to create an abstract phantom type. This
      allows deferring definitions of ``predicate`` and ``bound`` to concrete subtypes.
    """

    __predicate__: Predicate[T]
    # The bound of a phantom type is the type that its values will have at
    # runtime, so when checking if a value is an instance of a phantom type,
    # it's first checked to be within its bounds, so that the value can be
    # safely passed as argument to the predicate function.
    #
    # When subclassing, the bound of the new type must be a subtype of the bound
    # of the super class.
    __bound__: ClassVar[NotKnownMutableType]
    __abstract__: ClassVar[bool]

    def __init_subclass__(
        cls,
        predicate: Predicate[T] | None = None,
        bound: type[T] | None = None,
        abstract: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        resolve_class_attr(cls, "__abstract__", abstract)
        resolve_class_attr(cls, "__predicate__", predicate)
        cls._resolve_bound(bound)

    @classmethod
    def _interpret_implicit_bound(cls) -> BoundType:
        def discover_bounds() -> Iterable[type]:
            for type_ in cls.__mro__:
                if type_ is cls:
                    continue
                if issubclass(type_, Phantom):
                    break
                yield type_
            else:  # pragma: no cover
                raise RuntimeError(f"{cls} is not a subclass of Phantom")

        types = tuple(discover_bounds())
        if len(types) == 1:
            return types[0]
        return types

    @classmethod
    def _resolve_bound(cls, class_arg: Any) -> None:
        inherited = getattr(cls, "__bound__", None)
        implicit = cls._interpret_implicit_bound()
        if class_arg is not None:
            bound = class_arg
        elif implicit:
            bound = implicit
        elif inherited is not None:
            bound = inherited
        elif not getattr(cls, "__abstract__", False):
            raise UnresolvedClassAttribute(
                f"Concrete phantom type {cls.__qualname__} must define class attribute "
                f"__bound__."
            )
        else:
            return

        if inherited is not None and not is_subtype(bound, inherited):
            raise BoundError(
                f"The bounds of {cls.__qualname__} are not compatible with its "
                f"inherited bounds."
            )

        assert is_not_mutable_type(bound)

        cls.__bound__ = bound

    @classmethod
    def __instancecheck__(cls, instance: object) -> bool:
        if cls.__abstract__:
            raise AbstractInstanceCheck(
                "Abstract phantom types cannot be used in instance checks"
            )
        try:
            instance = get_bound_parser(cls.__bound__)(instance)
        except BoundError:
            return False
        return cls.__predicate__(instance)
