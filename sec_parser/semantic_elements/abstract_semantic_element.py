from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

from sec_parser.exceptions import SecParserValueError

if TYPE_CHECKING:  # pragma: no cover
    from sec_parser.processing_engine.html_tag import HtmlTag


class AbstractSemanticElement(ABC):  # noqa: B024
    """
    In the domain of HTML parsing, especially in the context of SEC EDGAR documents,
    a semantic element refers to a meaningful unit within the document that serves a
    specific purpose. For example, a paragraph or a table might be considered a
    semantic element. Unlike syntactic elements, which merely exist to structure the
    HTML, semantic elements carry information that is vital to the understanding of the
    document's content.

    This class serves as a foundational representation of such semantic elements,
    containing an HtmlTag object that stores the raw HTML tag information. Subclasses
    will implement additional behaviors based on the type of the semantic element.
    """

    def __init__(
        self,
        html_tag: HtmlTag,
    ) -> None:
        self.html_tag = html_tag

    @classmethod
    def convert_from(
        cls,
        source: AbstractSemanticElement,
    ) -> AbstractSemanticElement:
        """Convert the semantic element into another semantic element type."""
        return cls(source.html_tag)

    @classmethod
    def get_direct_abstract_semantic_subclass(
        cls,
    ) -> type[AbstractSemanticElement]:
        """
        Given a class, find the class that is one step below
        AbstractSemanticElement in its inheritance hierarchy.
        """
        if not issubclass(cls, AbstractSemanticElement):
            msg = "Argument must be a subclass of AbstractSemanticElement."
            raise TypeError(msg)

        root_child = None
        for ancestor in cls.mro():
            if ancestor is AbstractSemanticElement:
                break
            root_child = ancestor

        if root_child is None:
            msg = "Could not find a root child class for the given class."
            raise ValueError(msg)

        return root_child

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}<{self.html_tag.name}>"


class AbstractLevelElement(AbstractSemanticElement, ABC):
    """
    The AbstractLevelElement class provides a level attribute to semantic elements.
    It represents hierarchical levels in the document structure. For instance,
    a main section title might be at level 1, a subsection at level 2, etc.
    """

    MIN_LEVEL = 1

    def __init__(
        self,
        html_tag: HtmlTag,
        level: int | None = None,
    ) -> None:
        super().__init__(html_tag)
        level = level or self.MIN_LEVEL

        if level < self.MIN_LEVEL:
            msg = f"Level must be equal or greater than {self.MIN_LEVEL}"
            raise InvalidLevelError(msg)
        self.level = level

    @classmethod
    def convert_from(
        cls,
        source: AbstractSemanticElement,
        *,
        level: int | None = None,
    ) -> AbstractLevelElement:
        return cls(source.html_tag, level=level)


class InvalidLevelError(SecParserValueError):
    pass
