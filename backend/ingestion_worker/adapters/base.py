from abc import ABC, abstractmethod
from backend.shared.models import RawArticle


class FeedAdapter(ABC):
    """
    Abstract base class for all source adapters.
    Every adapter must implement fetch() and source_code().
    """

    @abstractmethod
    def source_code(self) -> str:
        """Returns the source code string e.g. 'AJA', 'NYT'"""
        pass

    @abstractmethod
    def fetch(self) -> list[RawArticle]:
        """
        Fetches latest articles from the source.
        Returns a list of RawArticle objects.
        Never raises — catches all errors internally and returns empty list.
        """
        pass